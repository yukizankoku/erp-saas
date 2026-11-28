"""Integrasi modul org pada skema hasil migrasi, lewat role aplikasi (RLS aktif)."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.ids import uuid7
from app.core.tenancy.context import tenant_context
from app.framework.errors import (
    CodeConflict,
    RecordInUse,
    RecordNotFound,
    RecordReferenceInvalid,
    VersionConflict,
)
from app.modules.org.repositories import WarehouseRepository
from app.modules.org.schemas import (
    BranchCreate,
    BranchUpdate,
    CompanyCreate,
    CompanyUpdate,
    WarehouseCreate,
    WarehouseUpdate,
)
from app.modules.org.services import BranchService, CompanyService, WarehouseService, create_tenant
from tests.integration.conftest import APP_TEST_ROLE
from tests.integration.test_rls_meta import _alembic_config

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture(scope="session")
def org_tables(database_url: str, rls_tables: None) -> Iterator[None]:
    """Skema org dari `alembic upgrade head` + hak DML role uji (di produksi: `app_runtime`)."""

    def upgrade(connection: Connection) -> None:
        command.upgrade(_alembic_config(connection), "head")

    async def setup() -> None:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(upgrade)
                await conn.execute(text(f"GRANT SELECT ON tenants TO {APP_TEST_ROLE}"))
                await conn.execute(
                    text(
                        "GRANT SELECT, INSERT, UPDATE, DELETE ON companies, branches, warehouses "
                        f"TO {APP_TEST_ROLE}"
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(setup())
    yield


@pytest.fixture
def factory(session_factory: Factory, org_tables: None) -> Factory:
    return session_factory


async def new_tenant(admin: AsyncEngine) -> UUID:
    async with AsyncSession(admin) as session:
        tenant = await create_tenant(session, name="T", slug=f"t-{uuid7().hex}")
        tenant_id = tenant.id
        await session.commit()
        return tenant_id


@asynccontextmanager
async def scope(factory: Factory, tenant: UUID) -> AsyncIterator[AsyncSession]:
    """Satu transaksi dalam konteks tenant; commit bila blok selesai tanpa error."""
    async with factory() as session:
        with tenant_context(tenant):
            yield session
            await session.commit()


def company_data(code: str = "HQ", **kw: Any) -> CompanyCreate:
    return CompanyCreate(code=code, legal_name="PT Contoh", **kw)


async def make_company(factory: Factory, tenant: UUID, code: str = "HQ") -> UUID:
    async with scope(factory, tenant) as s:
        return (await CompanyService(s).create(company_data(code))).id


async def test_company_invisible_to_other_tenant(
    factory: Factory, admin_engine: AsyncEngine
) -> None:
    a, b = await new_tenant(admin_engine), await new_tenant(admin_engine)
    company_id = await make_company(factory, a)
    async with scope(factory, a) as s:
        assert [c.id for c in (await CompanyService(s).list()).items] == [company_id]
    async with scope(factory, b) as s:
        with pytest.raises(RecordNotFound):
            await CompanyService(s).get(company_id)
        assert (await CompanyService(s).list()).items == []
        with pytest.raises(RecordNotFound):
            await CompanyService(s).get_settings(company_id)


async def test_company_stores_normalised_npwp(factory: Factory, admin_engine: AsyncEngine) -> None:
    t = await new_tenant(admin_engine)
    async with scope(factory, t) as s:
        company = await CompanyService(s).create(company_data(npwp="01.234.567.8-901.000"))
        assert company.npwp == "012345678901000"
        updated = await CompanyService(s).update(
            company.id, CompanyUpdate(npwp="0123456789012345"), expected_version=company.version
        )
        assert updated.npwp == "0123456789012345"
        assert updated.version == 2


async def test_branch_and_warehouse_bound_to_company(
    factory: Factory, admin_engine: AsyncEngine
) -> None:
    t = await new_tenant(admin_engine)
    c1, c2 = await make_company(factory, t, "C1"), await make_company(factory, t, "C2")
    async with scope(factory, t) as s:
        branch = await BranchService(s).create(BranchCreate(company_id=c1, code="B1", name="Pusat"))
        wh = await WarehouseService(s).create(
            WarehouseCreate(company_id=c1, branch_id=branch.id, code="W1", name="Gudang")
        )
        assert (wh.company_id, wh.branch_id) == (c1, branch.id)
        # Gudang tanpa cabang diperbolehkan.
        await WarehouseService(s).create(WarehouseCreate(company_id=c1, code="W2", name="Lepas"))
    async with scope(factory, t) as s:
        assert [b.code for b in (await BranchService(s).list_by_company(c1)).items] == ["B1"]
        assert [w.code for w in (await WarehouseService(s).list_by_company(c1)).items] == [
            "W1",
            "W2",
        ]
        assert (await BranchService(s).list_by_company(c2)).items == []
        assert (await WarehouseService(s).list_by_company(c2)).items == []


async def test_warehouse_with_other_company_branch_is_rejected(
    factory: Factory, admin_engine: AsyncEngine
) -> None:
    t, other = await new_tenant(admin_engine), await new_tenant(admin_engine)
    c1, c2 = await make_company(factory, t, "C1"), await make_company(factory, t, "C2")
    foreign_company = await make_company(factory, other, "X")
    async with scope(factory, t) as s:
        b2 = await BranchService(s).create(BranchCreate(company_id=c2, code="B", name="Cabang C2"))
        w1 = await WarehouseService(s).create(WarehouseCreate(company_id=c1, code="W", name="G"))
    # Service: cabang company lain, cabang tak ada, company tenant lain.
    for bad in (
        WarehouseCreate(company_id=c1, branch_id=b2.id, code="X1", name="n"),
        WarehouseCreate(company_id=c1, branch_id=uuid7(), code="X2", name="n"),
        WarehouseCreate(company_id=foreign_company, code="X3", name="n"),
    ):
        with pytest.raises(RecordReferenceInvalid):
            async with scope(factory, t) as s:
                await WarehouseService(s).create(bad)
    with pytest.raises(RecordReferenceInvalid):
        async with scope(factory, t) as s:
            await WarehouseService(s).update(
                w1.id, WarehouseUpdate(branch_id=b2.id), expected_version=w1.version
            )
    # DB: FK 3 kolom menolak walau service dilewati (repository langsung).
    with pytest.raises(RecordReferenceInvalid):
        async with scope(factory, t) as s:
            await WarehouseRepository(s).create(
                {"company_id": c1, "branch_id": b2.id, "code": "RAW", "name": "n"}
            )


async def test_code_unique_per_tenant_and_reusable_after_delete(
    factory: Factory, admin_engine: AsyncEngine
) -> None:
    a, b = await new_tenant(admin_engine), await new_tenant(admin_engine)
    company_a = await make_company(factory, a, "HQ")
    await make_company(factory, b, "HQ")  # kode sama di tenant lain: boleh
    with pytest.raises(CodeConflict):
        await make_company(factory, a, "HQ")
    async with scope(factory, a) as s:
        await BranchService(s).create(BranchCreate(company_id=company_a, code="B", name="n"))
    with pytest.raises(CodeConflict):
        async with scope(factory, a) as s:
            await BranchService(s).create(BranchCreate(company_id=company_a, code="B", name="n2"))
    with pytest.raises(CodeConflict):
        async with scope(factory, a) as s:
            await WarehouseService(s).create(
                WarehouseCreate(company_id=company_a, code="W", name="n")
            )
            await WarehouseService(s).create(
                WarehouseCreate(company_id=company_a, code="W", name="n")
            )
    # Setelah soft delete, kode dapat dipakai lagi (unique parsial).
    async with scope(factory, a) as s:
        wh = (await WarehouseService(s).list_by_company(company_a)).items
    assert wh == []  # transaksi gagal di atas di-rollback seluruhnya
    async with scope(factory, a) as s:
        w = await WarehouseService(s).create(
            WarehouseCreate(company_id=company_a, code="W", name="1")
        )
        await WarehouseService(s).soft_delete(w.id, expected_version=w.version)
        await WarehouseService(s).create(WarehouseCreate(company_id=company_a, code="W", name="2"))


async def test_soft_delete_hides_record_and_blocks_parent_with_children(
    factory: Factory, admin_engine: AsyncEngine
) -> None:
    t = await new_tenant(admin_engine)
    company = await make_company(factory, t)
    async with scope(factory, t) as s:
        branch = await BranchService(s).create(BranchCreate(company_id=company, code="B", name="n"))
        wh = await WarehouseService(s).create(
            WarehouseCreate(company_id=company, branch_id=branch.id, code="W", name="n")
        )
    async with scope(factory, t) as s:
        company_row = await CompanyService(s).get(company)
        with pytest.raises(RecordInUse):
            await CompanyService(s).soft_delete(company, expected_version=company_row.version)
        with pytest.raises(RecordInUse):
            await BranchService(s).soft_delete(branch.id, expected_version=branch.version)
    async with scope(factory, t) as s:
        await WarehouseService(s).soft_delete(wh.id, expected_version=wh.version)
        await BranchService(s).soft_delete(branch.id, expected_version=branch.version)
        await CompanyService(s).soft_delete(company, expected_version=company_row.version)
    async with scope(factory, t) as s:
        for coro in (
            WarehouseService(s).get(wh.id),
            BranchService(s).get(branch.id),
            CompanyService(s).get(company),
        ):
            with pytest.raises(RecordNotFound):
                await coro
        assert (await CompanyService(s).list()).items == []
        assert (await BranchService(s).list_by_company(company)).items == []


async def test_branch_timezone_falls_back_to_company(
    factory: Factory, admin_engine: AsyncEngine
) -> None:
    t = await new_tenant(admin_engine)
    async with scope(factory, t) as s:
        company = await CompanyService(s).create(company_data(timezone="Asia/Makassar"))
        svc = BranchService(s)
        b = await svc.create(BranchCreate(company_id=company.id, code="B", name="n"))
        assert await svc.effective_timezone(b) == "Asia/Makassar"
        b = await svc.update(
            b.id, BranchUpdate(timezone="Asia/Jayapura"), expected_version=b.version
        )
        assert await svc.effective_timezone(b) == "Asia/Jayapura"


async def test_company_settings_shallow_merge(factory: Factory, admin_engine: AsyncEngine) -> None:
    t = await new_tenant(admin_engine)
    company = await make_company(factory, t)
    async with scope(factory, t) as s:
        svc = CompanyService(s)
        assert await svc.get_settings(company) == {}
        version = (await svc.get(company)).version
        first = await svc.update_settings(
            company,
            {"negative_stock": False, "valuation": {"method": "avg"}},
            expected_version=version,
        )
        assert first == {"negative_stock": False, "valuation": {"method": "avg"}}
    async with scope(factory, t) as s:
        svc = CompanyService(s)
        version = (await svc.get(company)).version
        merged = await svc.update_settings(
            company,
            {"modules": ["sales"], "valuation": {"method": "fifo"}},
            expected_version=version,
        )
        # Dangkal: kunci level atas ditimpa utuh, kunci lain dipertahankan.
        assert merged == {
            "negative_stock": False,
            "modules": ["sales"],
            "valuation": {"method": "fifo"},
        }
        with pytest.raises(VersionConflict):
            await svc.update_settings(company, {"x": 1}, expected_version=version)
    async with scope(factory, t) as s:
        assert await CompanyService(s).get_settings(company) == merged
