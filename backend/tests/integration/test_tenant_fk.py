"""FK komposit lintas tenant ditolak DB dan tidak menjadi oracle (ADR 0004 §4a, #37)."""

from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.core.tenancy.context import tenant_context
from app.framework.errors import RecordReferenceInvalid, register_record_error_handlers
from app.framework.repository import TenantRepository
from tests.integration.models import Item, ItemChild, ItemRepository

SessionFactory = async_sessionmaker[AsyncSession]


class ChildRepository(TenantRepository[ItemChild]):
    model = ItemChild


async def _item(factory: SessionFactory, tenant: UUID) -> Item:
    async with factory() as session:
        with tenant_context(tenant):
            item = await ItemRepository(session).create({"name": "parent"})
            await session.commit()
    return item


async def _child_count(admin: AsyncEngine, label: str) -> int:
    async with admin.connect() as conn:
        n = await conn.scalar(
            text("SELECT count(*) FROM rls_test_item_children WHERE label = :l"), {"l": label}
        )
    return int(n or 0)


async def test_same_tenant_reference_works(session_factory: SessionFactory) -> None:
    tenant = uuid7()
    parent = await _item(session_factory, tenant)
    async with session_factory() as session:
        with tenant_context(tenant):
            child = await ChildRepository(session).create({"item_id": parent.id, "label": "ok"})
            await session.commit()
    assert child.item_id == parent.id


@pytest.mark.parametrize("client_id", [False, True])
async def test_foreign_and_missing_parent_give_identical_error(
    session_factory: SessionFactory, admin_engine: AsyncEngine, client_id: bool
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    foreign = await _item(session_factory, tenant_b)
    errors = []
    for parent_id in (foreign.id, uuid7()):  # milik tenant B vs tidak ada
        values: dict[str, object] = {"item_id": parent_id, "label": "bad"}
        if client_id:
            values["id"] = uuid7()
        async with session_factory() as session:
            with tenant_context(tenant_a), pytest.raises(RecordReferenceInvalid) as exc:
                await ChildRepository(session).create(values)
        errors.append((exc.value.code, exc.value.status_code, str(exc.value)))
    assert errors[0] == errors[1]
    assert errors[0][:2] == ("record.reference_invalid", 422)
    assert await _child_count(admin_engine, "bad") == 0


async def test_reference_error_keeps_other_writes_and_update_maps_too(
    session_factory: SessionFactory,
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    own = await _item(session_factory, tenant_a)
    foreign = await _item(session_factory, tenant_b)
    async with session_factory() as session:
        with tenant_context(tenant_a):
            repo = ChildRepository(session)
            child = await repo.create({"item_id": own.id, "label": "c"})
            with pytest.raises(RecordReferenceInvalid):
                await repo.create({"item_id": foreign.id, "label": "x"})
            with pytest.raises(RecordReferenceInvalid):
                await repo.update(child.id, {"item_id": foreign.id}, expected_version=child.version)
            assert session.in_transaction()
            kept = await repo.create({"item_id": own.id, "label": "kept"})
            await session.commit()
    assert kept.label == "kept"


@pytest.mark.parametrize("op", ["create", "update"])
async def test_callers_pending_invalid_write_is_not_mapped_to_reference_invalid(
    session_factory: SessionFactory, op: str
) -> None:
    tenant = uuid7()
    own = await _item(session_factory, tenant)
    async with session_factory() as session:
        with tenant_context(tenant):
            repo = ChildRepository(session)
            child = await repo.create({"item_id": own.id, "label": "c"})
            # Tulisan tertunda pemanggil dengan FK tidak valid (belum di-flush).
            session.add(ChildRepository.model(tenant_id=tenant, item_id=uuid7(), label="pending"))
            with pytest.raises(IntegrityError):
                if op == "create":
                    await repo.create({"item_id": own.id, "label": "ok"})
                else:
                    await repo.update(child.id, {"label": "z"}, expected_version=child.version)
            await session.rollback()


async def test_reference_invalid_http_response_is_identical(
    session_factory: SessionFactory,
) -> None:
    app = FastAPI()
    register_record_error_handlers(app)

    @app.get("/x")
    async def _x() -> None:
        raise RecordReferenceInvalid()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        for lang, title in (
            ("id", "Referensi data tidak valid"),
            ("en", "Invalid record reference"),
        ):
            r = await client.get("/x", headers={"Accept-Language": lang})
            assert r.status_code == 422
            assert r.json()["code"] == "record.reference_invalid"
            assert r.json()["title"] == title
