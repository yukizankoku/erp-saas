"""Celah tambahan QA untuk P1-01 (ADR 0004 §8): batas isolasi repository & siklus RLS up/down."""

from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.core.tenancy.context import tenant_context
from app.core.tenancy.rls import drop_tenant_rls_sql, tenant_rls_sql
from app.framework.errors import RecordConflict, RecordNotFound
from app.main import create_app
from tests.integration.models import Item, ItemRepository
from tests.integration.test_rls_meta import audit_tenant_tables

SessionFactory = async_sessionmaker[AsyncSession]


async def _admin_rows(admin: AsyncEngine, row_id: UUID) -> list[dict[str, Any]]:
    async with admin.connect() as conn:
        result = await conn.execute(
            text("SELECT * FROM rls_test_items WHERE id = :id"), {"id": row_id}
        )
        return [dict(row) for row in result.mappings()]


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


async def test_create_with_id_owned_by_other_tenant_raises_domain_error(
    session_factory: SessionFactory, admin_engine: AsyncEngine
) -> None:
    """Id dari klien (offline) yang sudah dipakai tenant lain → 409 tanpa membocorkan baris itu."""
    shared_id = uuid7()
    owner = await _create(session_factory, uuid7(), id=shared_id)
    async with session_factory() as session:
        with tenant_context(uuid7()), pytest.raises(RecordConflict) as exc:
            await ItemRepository(session).create({"id": shared_id, "name": "clash"})
        assert session.in_transaction()  # hanya savepoint insert yang batal; transaksi tetap aktif
    assert (exc.value.status_code, exc.value.code) == (409, "record.conflict")
    assert "clash" not in str(exc.value) and str(shared_id) not in str(exc.value)
    row = (await _admin_rows(admin_engine, shared_id))[0]
    assert (row["tenant_id"], row["name"]) == (owner.tenant_id, "item")


async def test_create_with_duplicate_id_same_tenant_is_rejected(
    session_factory: SessionFactory, admin_engine: AsyncEngine
) -> None:
    tenant, dup = uuid7(), uuid7()
    await _create(session_factory, tenant, id=dup)
    async with session_factory() as session:
        with tenant_context(tenant):
            with pytest.raises(RecordConflict) as exc:
                await ItemRepository(session).create({"id": dup, "name": "again"})
            assert (exc.value.status_code, exc.value.code) == (409, "record.conflict")
            # Hanya insert yang bentrok yang batal (savepoint); transaksi tetap dapat dipakai.
            again = await ItemRepository(session).create({"name": "next"})
            await session.commit()
    assert [r["name"] for r in await _admin_rows(admin_engine, dup)] == ["item"]
    assert [r["name"] for r in await _admin_rows(admin_engine, again.id)] == ["next"]


async def test_create_conflict_inside_savepoint_keeps_outer_transaction(
    session_factory: SessionFactory, admin_engine: AsyncEngine
) -> None:
    tenant, dup = uuid7(), uuid7()
    await _create(session_factory, tenant, id=dup)
    async with session_factory() as session:
        repo = ItemRepository(session)
        with tenant_context(tenant):
            outer = await repo.create({"name": "outer"})
            with pytest.raises(RecordConflict):
                async with session.begin_nested():  # hanya savepoint ini yang batal
                    await repo.create({"id": dup, "name": "dup"})
            assert await repo.get(outer.id) is not None
            await session.commit()
    assert [r["name"] for r in await _admin_rows(admin_engine, outer.id)] == ["outer"]


async def test_create_conflict_keeps_earlier_writes_of_the_caller_transaction(
    session_factory: SessionFactory, admin_engine: AsyncEngine
) -> None:
    """Review security: konflik id tidak boleh me-rollback tulisan lain di transaksi pemanggil."""
    tenant, dup = uuid7(), uuid7()
    await _create(session_factory, tenant, id=dup)
    async with session_factory() as session:
        repo = ItemRepository(session)
        with tenant_context(tenant):
            before = await repo.create({"name": "before"})
            with pytest.raises(RecordConflict):
                await repo.create({"id": dup, "name": "dup"})
            after = await repo.create({"name": "after"})
            await session.commit()
    assert [r["name"] for r in await _admin_rows(admin_engine, before.id)] == ["before"]
    assert [r["name"] for r in await _admin_rows(admin_engine, after.id)] == ["after"]
    assert [r["name"] for r in await _admin_rows(admin_engine, dup)] == ["item"]


async def test_create_with_other_unique_violation_is_not_reported_as_id_conflict(
    session_factory: SessionFactory,
) -> None:
    tenant = uuid7()
    await _create(session_factory, tenant, code="SAME")
    async with session_factory() as session:
        with tenant_context(tenant), pytest.raises(IntegrityError) as exc:
            await ItemRepository(session).create({"name": "x", "code": "SAME"})
    assert "uq_rls_test_items_tenant_id_code" in str(exc.value)


async def test_id_conflict_http_response_is_identical_for_own_and_foreign_ids(
    session_factory: SessionFactory,
) -> None:
    tenant = uuid7()
    own = await _create(session_factory, tenant)
    foreign = await _create(session_factory, uuid7())
    app = create_app()

    @app.post("/_test/items/{item_id}")
    async def create_item(item_id: UUID) -> dict[str, str]:
        with tenant_context(tenant):
            async with session_factory() as session:
                record = await ItemRepository(session).create({"id": item_id, "name": "post"})
                await session.commit()
                return {"id": str(record.id)}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = {
            name: [
                await client.post(f"/_test/items/{item_id}", headers={"Accept-Language": lang})
                for lang in ("id", "en")
            ]
            for name, item_id in (("own", own.id), ("foreign", foreign.id))
        }
        created = await client.post(f"/_test/items/{uuid7()}")

    assert created.status_code == 200
    for own_response, foreign_response in zip(responses["own"], responses["foreign"], strict=True):
        assert own_response.status_code == foreign_response.status_code == 409
        assert own_response.headers["content-type"] == "application/problem+json"
        assert own_response.json() == foreign_response.json()
        assert own_response.json()["code"] == "record.conflict"
    assert responses["own"][0].json()["title"] != responses["own"][1].json()["title"]  # id vs en


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
