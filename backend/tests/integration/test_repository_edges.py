"""Celah tambahan QA untuk P1-01 (ADR 0004 §8): batas isolasi repository & siklus RLS up/down."""

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.core.tenancy.context import tenant_context
from app.core.tenancy.rls import drop_tenant_rls_sql, tenant_rls_sql
from app.framework.errors import RecordError, RecordNotFound
from tests.integration.models import Item, ItemRepository
from tests.integration.test_rls_meta import audit_tenant_tables

SessionFactory = async_sessionmaker[AsyncSession]


async def _create(factory: SessionFactory, tenant: UUID, **values: object) -> Item:
    values.setdefault("name", "item")
    async with factory() as session:
        with tenant_context(tenant):
            item = await ItemRepository(session).create(values)
            await session.commit()
    return item


async def test_paginated_list_never_leaks_across_tenants(session_factory: SessionFactory) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    # Selang-seling agar id (UUIDv7, urut waktu) kedua tenant bercampur dalam urutan global.
    a_ids: set[UUID] = set()
    b_ids: set[UUID] = set()
    for i in range(4):
        a_ids.add((await _create(session_factory, tenant_a, name=f"a{i}")).id)
        b_ids.add((await _create(session_factory, tenant_b, name=f"b{i}")).id)

    for tenant, own, other in ((tenant_a, a_ids, b_ids), (tenant_b, b_ids, a_ids)):
        seen: list[UUID] = []
        async with session_factory() as session:  # satu sesi/transaksi per tenant
            repo = ItemRepository(session)
            with tenant_context(tenant):
                page_no = 1
                while True:
                    page = await repo.list(page=page_no, page_size=3)
                    seen.extend(i.id for i in page.items)
                    if not page.has_more:
                        break
                    page_no += 1
            assert set(seen) == own
            assert len(seen) == len(own)
            assert not set(seen) & other


async def test_soft_deleted_record_cannot_be_updated_or_deleted_again(
    session_factory: SessionFactory, admin_engine: AsyncEngine
) -> None:
    tenant = uuid7()
    item = await _create(session_factory, tenant)
    async with session_factory() as session:
        repo = ItemRepository(session)
        with tenant_context(tenant):
            await repo.soft_delete(item.id, expected_version=1)
            await session.commit()
            for version in (1, 2):  # versi lama maupun versi terkini sesudah hapus
                with pytest.raises(RecordNotFound):
                    await repo.update(item.id, {"name": "zombie"}, expected_version=version)
                with pytest.raises(RecordNotFound):
                    await repo.soft_delete(item.id, expected_version=version)
    async with admin_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT name, version FROM rls_test_items WHERE id = :id"), {"id": item.id}
            )
        ).one()
    assert tuple(row) == ("item", 2)


@pytest.mark.xfail(
    strict=True,
    raises=IntegrityError,
    reason=(
        "Bug P1-01: id dari klien (offline) yang sudah dipakai tenant lain menembus ke "
        "IntegrityError mentah (HTTP 500). Seharusnya RecordError 4xx (409) tanpa membocorkan "
        "keberadaan baris tenant lain."
    ),
)
async def test_create_with_id_owned_by_other_tenant_raises_domain_error(
    session_factory: SessionFactory,
) -> None:
    shared_id = uuid7()
    await _create(session_factory, uuid7(), id=shared_id)
    async with session_factory() as session:
        with tenant_context(uuid7()), pytest.raises(RecordError) as exc:
            await ItemRepository(session).create({"id": shared_id, "name": "clash"})
    assert 400 <= exc.value.status_code < 500


async def test_create_with_duplicate_id_same_tenant_is_rejected(
    session_factory: SessionFactory,
) -> None:
    tenant, dup = uuid7(), uuid7()
    await _create(session_factory, tenant, id=dup)
    async with session_factory() as session:
        with tenant_context(tenant), pytest.raises(IntegrityError):
            await ItemRepository(session).create({"id": dup, "name": "again"})


async def test_rls_helpers_upgrade_downgrade_roundtrip(admin_engine: AsyncEngine) -> None:
    """Helper RLS (dipakai migrasi up/down) benar-benar menambah & menghapus proteksi di PG."""
    table = "rls_roundtrip_probe"

    async def audit_problems() -> list[str] | None:
        async with admin_engine.connect() as conn:
            checked, problems = await audit_tenant_tables(conn)
        return None if f"public.{table}" not in checked else problems

    async with admin_engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        await conn.execute(
            text(f"CREATE TABLE {table} (id uuid PRIMARY KEY, tenant_id uuid NOT NULL)")
        )
        await conn.execute(text(f"CREATE INDEX ix_{table}_tenant_id ON {table} (tenant_id)"))
    try:
        before = await audit_problems()
        assert before is not None and any("RLS tidak aktif" in p for p in before)

        async with admin_engine.begin() as conn:
            for stmt in tenant_rls_sql(table):  # upgrade
                await conn.execute(text(stmt))
        assert await audit_problems() == []

        async with admin_engine.begin() as conn:
            for stmt in drop_tenant_rls_sql(table):  # downgrade
                await conn.execute(text(stmt))
            policies = await conn.scalar(
                text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}
            )
        assert policies == 0
        after = await audit_problems()
        assert after is not None and any("RLS tidak aktif" in p for p in after)

        async with admin_engine.begin() as conn:  # upgrade ulang idempoten setelah downgrade
            for stmt in tenant_rls_sql(table):
                await conn.execute(text(stmt))
        assert await audit_problems() == []
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
