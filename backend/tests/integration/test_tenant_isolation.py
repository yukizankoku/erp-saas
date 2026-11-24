"""Isolasi tenant berlapis: repository (lapis 1) dan RLS PostgreSQL (lapis 2) — ADR 0004 §8.

Setiap test memakai tenant acak baru sehingga tidak saling mengganggu walau tabel dipakai bersama.
"""

from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.core.tenancy.context import TenantContextMissing, tenant_context
from app.core.tenancy.session import SESSION_INFO_KEY, TenantRebindError, ensure_tenant_bound
from app.framework.errors import RecordNotFound, VersionConflict
from app.framework.repository import TenantRepository
from app.main import create_app
from tests.integration.models import Item, ItemRepository

SessionFactory = async_sessionmaker[AsyncSession]
RLS_VIOLATION = "42501"  # insufficient_privilege: "new row violates row-level security policy"


async def _create(
    factory: SessionFactory, tenant: UUID, user: UUID | None = None, **values: Any
) -> Item:
    values.setdefault("name", "item")
    async with factory() as session:
        with tenant_context(tenant, user_id=user):
            item = await ItemRepository(session).create(values)
            await session.commit()
    return item


async def _seed(admin: AsyncEngine, tenant: UUID, name: str = "seed") -> UUID:
    """Sisipkan baris langsung sebagai superuser (melewati RLS) — data pihak lain."""
    row_id = uuid7()
    async with admin.begin() as conn:
        await conn.execute(
            text("INSERT INTO rls_test_items (id, tenant_id, name) VALUES (:id, :tid, :name)"),
            {"id": row_id, "tid": tenant, "name": name},
        )
    return row_id


async def _admin_row(admin: AsyncEngine, row_id: UUID) -> dict[str, Any]:
    async with admin.connect() as conn:
        result = await conn.execute(
            text("SELECT * FROM rls_test_items WHERE id = :id"), {"id": row_id}
        )
        return dict(result.mappings().one())


def _assert_rls_violation(exc: pytest.ExceptionInfo[DBAPIError]) -> None:
    assert getattr(exc.value.orig, "sqlstate", None) == RLS_VIOLATION, exc.value
    assert "row-level security" in str(exc.value)


# --- guard ---------------------------------------------------------------------------------


async def test_app_role_cannot_bypass_rls(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT current_user, r.rolsuper, r.rolbypassrls, "
                    "has_table_privilege('rls_test_items', 'SELECT'), "
                    "pg_has_role(current_user, (SELECT relowner FROM pg_class "
                    "  WHERE oid = 'rls_test_items'::regclass), 'MEMBER') "
                    "FROM pg_roles r WHERE r.rolname = current_user"
                )
            )
        ).one()
    # Bukan superuser, bukan BYPASSRLS, bukan pemilik tabel, tetapi punya hak DML.
    assert tuple(row) == ("app_rls_test", False, False, True, False)


# --- lapis 1: repository -------------------------------------------------------------------


async def test_repository_isolates_tenants(
    session_factory: SessionFactory, admin_engine: AsyncEngine
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    a1 = await _create(session_factory, tenant_a, name="a1")
    a2 = await _create(session_factory, tenant_a, name="a2")
    b1 = await _create(session_factory, tenant_b, name="b1")
    assert {a1.tenant_id, a2.tenant_id, b1.tenant_id} == {tenant_a, tenant_b}

    async with session_factory() as session:
        repo = ItemRepository(session)
        with tenant_context(tenant_a):
            page = await repo.list()
            assert {i.id for i in page.items} == {a1.id, a2.id}
            assert await repo.get(a1.id) is not None
            assert await repo.get(b1.id) is None
            with pytest.raises(RecordNotFound):
                await repo.update(b1.id, {"name": "stolen"}, expected_version=1)
            with pytest.raises(RecordNotFound):
                await repo.soft_delete(b1.id, expected_version=1)
            await session.commit()
        with tenant_context(tenant_b):
            assert [i.id for i in (await repo.list()).items] == [b1.id]

    row = await _admin_row(admin_engine, b1.id)
    assert (row["name"], row["version"], row["deleted_at"]) == ("b1", 1, None)


async def test_repository_create_fills_standard_columns(session_factory: SessionFactory) -> None:
    tenant, user = uuid7(), uuid7()
    client_id = uuid7()
    item = await _create(session_factory, tenant, user, id=client_id, code="C-1")
    # Nilai server tersedia tanpa lazy load (eager_defaults + RETURNING; tanpa MissingGreenlet).
    assert item.id == client_id  # id dari klien (offline) dipertahankan
    assert item.tenant_id == tenant
    assert (item.created_by, item.updated_by) == (user, user)
    assert item.created_at is not None and item.created_at.tzinfo is not None
    assert item.updated_at == item.created_at
    assert (item.version, item.custom_fields, item.deleted_at) == (1, {}, None)

    generated = await _create(session_factory, tenant)
    assert generated.id.version == 7


async def test_repository_list_paginates_by_id_desc(session_factory: SessionFactory) -> None:
    tenant = uuid7()
    created = [await _create(session_factory, tenant, name=f"n{i}") for i in range(5)]
    expected = sorted((i.id for i in created), reverse=True)
    async with session_factory() as session:
        repo = ItemRepository(session)
        with tenant_context(tenant):
            pages = [await repo.list(page=p, page_size=2) for p in (1, 2, 3, 4)]
    assert [[i.id for i in p.items] for p in pages] == [
        expected[0:2],
        expected[2:4],
        expected[4:5],
        [],
    ]
    assert [p.has_more for p in pages] == [True, True, False, False]


async def test_repository_requires_context(session_factory: SessionFactory) -> None:
    async with session_factory() as session:
        repo = ItemRepository(session)
        with pytest.raises(TenantContextMissing):
            await repo.list()
        with pytest.raises(TenantContextMissing):
            await repo.get(uuid7())
        with pytest.raises(TenantContextMissing):
            await repo.create({"name": "x"})


# --- lapis 2: RLS pada SQL mentah ----------------------------------------------------------


async def test_raw_sql_is_isolated_by_rls(
    session_factory: SessionFactory, admin_engine: AsyncEngine
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    row_a = await _seed(admin_engine, tenant_a, "a")
    row_b = await _seed(admin_engine, tenant_b, "b")

    async with session_factory() as session:
        with tenant_context(tenant_a):
            # Tanpa WHERE tenant_id sama sekali: RLS yang menyaring.
            rows = (await session.execute(text("SELECT id, tenant_id FROM rls_test_items"))).all()
            assert row_a in {r.id for r in rows}
            assert row_b not in {r.id for r in rows}
            assert {r.tenant_id for r in rows} == {tenant_a}

            updated = await session.execute(
                text("UPDATE rls_test_items SET name = 'hacked' WHERE id = :id"), {"id": row_b}
            )
            deleted = await session.execute(
                text("DELETE FROM rls_test_items WHERE id = :id"), {"id": row_b}
            )
            assert (updated.rowcount, deleted.rowcount) == (0, 0)  # type: ignore[attr-defined]
            await session.commit()

    for statement, params in [
        # INSERT dengan tenant_id B di konteks A.
        (
            "INSERT INTO rls_test_items (id, tenant_id, name) VALUES (:id, :tid, 'x')",
            {"id": uuid7(), "tid": tenant_b},
        ),
        # Memindahkan baris sendiri ke tenant lain.
        (
            "UPDATE rls_test_items SET tenant_id = :tid WHERE id = :id",
            {"id": row_a, "tid": tenant_b},
        ),
    ]:
        async with session_factory() as session:
            with tenant_context(tenant_a), pytest.raises(DBAPIError) as exc:
                await session.execute(text(statement), params)
            _assert_rls_violation(exc)

    row = await _admin_row(admin_engine, row_b)
    assert (row["tenant_id"], row["name"]) == (tenant_b, "b")
    assert (await _admin_row(admin_engine, row_a))["tenant_id"] == tenant_a


async def test_without_context_rls_fails_closed(
    session_factory: SessionFactory, admin_engine: AsyncEngine
) -> None:
    tenant = uuid7()
    await _seed(admin_engine, tenant)
    async with admin_engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM rls_test_items")) > 0

    async with session_factory() as session:
        assert await session.scalar(text("SELECT current_setting('app.tenant_id', true)")) is None
        assert await session.scalar(text("SELECT count(*) FROM rls_test_items")) == 0
        updated = await session.execute(text("UPDATE rls_test_items SET name = 'x'"))
        assert updated.rowcount == 0  # type: ignore[attr-defined]

    async with session_factory() as session:
        with pytest.raises(DBAPIError) as exc:
            await session.execute(
                text("INSERT INTO rls_test_items (id, tenant_id, name) VALUES (:id, :tid, 'x')"),
                {"id": uuid7(), "tid": tenant},
            )
        _assert_rls_violation(exc)


async def test_context_is_cleared_after_commit_on_same_connection(
    app_engine: AsyncEngine, admin_engine: AsyncEngine
) -> None:
    tenant = uuid7()
    await _seed(admin_engine, tenant)
    async with app_engine.connect() as conn:
        session = AsyncSession(bind=conn)
        pid = await session.scalar(text("SELECT pg_backend_pid()"))
        await session.commit()
        with tenant_context(tenant):
            setting = text("SELECT current_setting('app.tenant_id', true)")
            assert await session.scalar(setting) == str(tenant)
            assert await session.scalar(text("SELECT count(*) FROM rls_test_items")) == 1
            await session.commit()
        # Koneksi fisik yang sama, transaksi baru tanpa konteks: nilai '' (bukan NULL) → NULLIF.
        assert await session.scalar(text("SELECT pg_backend_pid()")) == pid
        assert await session.scalar(setting) == ""
        assert await session.scalar(text("SELECT count(*) FROM rls_test_items")) == 0
        await session.close()


async def test_ensure_tenant_bound_rebinds_transaction_started_without_context(
    session_factory: SessionFactory, admin_engine: AsyncEngine
) -> None:
    tenant = uuid7()
    row_id = await _seed(admin_engine, tenant)
    async with session_factory() as session:
        # Mis. lookup autentikasi: transaksi dimulai sebelum konteks tenant diset.
        await session.execute(text("SELECT 1"))
        assert session.info[SESSION_INFO_KEY] is None
        with tenant_context(tenant):
            # Tanpa ensure_tenant_bound, SQL di transaksi ini diam-diam melihat 0 baris.
            assert await session.scalar(text("SELECT count(*) FROM rls_test_items")) == 0
            assert await ensure_tenant_bound(session) == tenant
            assert session.info[SESSION_INFO_KEY] == tenant
            assert await session.scalar(text("SELECT count(*) FROM rls_test_items")) == 1
            # Repository memanggil ensure_tenant_bound sendiri.
        async with session_factory() as other:
            await other.execute(text("SELECT 1"))
            with tenant_context(tenant):
                assert await ItemRepository(other).get(row_id) is not None


async def test_ensure_tenant_bound_refuses_switching_tenant_mid_transaction(
    session_factory: SessionFactory, admin_engine: AsyncEngine
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    row_b = await _seed(admin_engine, tenant_b)
    async with session_factory() as session:
        repo = ItemRepository(session)
        with tenant_context(tenant_a):
            await repo.list()
        with tenant_context(tenant_b):
            with pytest.raises(TenantRebindError):
                await repo.get(row_b)
            await session.rollback()
            assert await repo.get(row_b) is not None  # transaksi baru → terikat ke B


# --- soft delete & versi -------------------------------------------------------------------


async def test_soft_delete_hides_row_and_records_batch(
    session_factory: SessionFactory, admin_engine: AsyncEngine
) -> None:
    tenant, user = uuid7(), uuid7()
    item = await _create(session_factory, tenant, code="DUP")
    other = await _create(session_factory, tenant, code="KEEP")

    async with session_factory() as session:
        repo = ItemRepository(session)
        with tenant_context(tenant, user_id=user):
            batch = await repo.soft_delete(item.id, expected_version=1)
            await session.commit()
            assert await repo.get(item.id) is None
            assert [i.id for i in (await repo.list()).items] == [other.id]
            with pytest.raises(RecordNotFound):
                await repo.update(item.id, {"name": "x"}, expected_version=2)
            with pytest.raises(RecordNotFound):
                await repo.soft_delete(item.id, expected_version=2)
            # Unique parsial WHERE deleted_at IS NULL: kode yang sama boleh dipakai lagi.
            await repo.create({"name": "again", "code": "DUP"})
            await session.commit()

    row = await _admin_row(admin_engine, item.id)
    assert row["deleted_at"] is not None
    assert (row["deleted_by"], row["updated_by"]) == (user, user)
    assert (row["delete_batch_id"], row["version"]) == (batch, 2)
    assert batch.version == 7

    shared_batch = uuid7()
    async with session_factory() as session:
        with tenant_context(tenant):
            assert (
                await ItemRepository(session).soft_delete(
                    other.id, expected_version=1, batch_id=shared_batch
                )
                == shared_batch
            )
            await session.commit()
    assert (await _admin_row(admin_engine, other.id))["delete_batch_id"] == shared_batch


async def test_update_bumps_version_and_rejects_stale_version(
    session_factory: SessionFactory, admin_engine: AsyncEngine
) -> None:
    tenant, creator, editor = uuid7(), uuid7(), uuid7()
    item = await _create(session_factory, tenant, creator)

    async with session_factory() as session:
        repo = ItemRepository(session)
        with tenant_context(tenant, user_id=editor):
            updated = await repo.update(
                item.id, {"name": "renamed", "custom_fields": {"color": "red"}}, expected_version=1
            )
            assert updated.version == 2
            await session.commit()
            with pytest.raises(VersionConflict):
                await repo.update(item.id, {"name": "lost update"}, expected_version=1)

    row = await _admin_row(admin_engine, item.id)
    assert (row["name"], row["version"], row["custom_fields"]) == ("renamed", 2, {"color": "red"})
    assert (row["created_by"], row["updated_by"]) == (creator, editor)
    assert row["updated_at"] > row["created_at"]


async def test_concurrent_update_after_check_raises_version_conflict(
    session_factory: SessionFactory, admin_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid7()
    item = await _create(session_factory, tenant)
    original = TenantRepository._get_for_write

    async def racing(self: ItemRepository, id: UUID, expected_version: int) -> Item:
        record = await original(self, id, expected_version)
        # Penulis lain menang di antara pengecekan versi dan flush.
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("UPDATE rls_test_items SET version = version + 1 WHERE id = :id"), {"id": id}
            )
        return record

    monkeypatch.setattr(TenantRepository, "_get_for_write", racing)
    async with session_factory() as session:
        with tenant_context(tenant), pytest.raises(VersionConflict) as exc:
            await ItemRepository(session).update(item.id, {"name": "x"}, expected_version=1)
        assert type(exc.value.__cause__).__name__ == "StaleDataError"
    assert (await _admin_row(admin_engine, item.id))["name"] == "item"


async def test_version_conflict_returns_http_409(
    session_factory: SessionFactory,
) -> None:
    tenant = uuid7()
    item = await _create(session_factory, tenant)
    foreign = await _create(session_factory, uuid7())
    app = create_app()

    @app.patch("/_test/items/{item_id}")
    async def patch_item(item_id: UUID, expected_version: int) -> dict[str, int]:
        with tenant_context(tenant):
            async with session_factory() as session:
                record = await ItemRepository(session).update(
                    item_id, {"name": "via http"}, expected_version=expected_version
                )
                await session.commit()
                return {"version": record.version}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.patch(f"/_test/items/{item.id}", params={"expected_version": 1})
        stale = await client.patch(
            f"/_test/items/{item.id}",
            params={"expected_version": 1},
            headers={"Accept-Language": "en"},
        )
        other_tenant = await client.patch(
            f"/_test/items/{foreign.id}", params={"expected_version": 1}
        )

    assert (ok.status_code, ok.json()) == (200, {"version": 2})
    assert stale.status_code == 409
    assert stale.headers["content-type"] == "application/problem+json"
    assert stale.json()["code"] == "record.version_conflict"
    assert stale.json()["title"] == "Record version conflict"
    assert other_tenant.status_code == 404
    assert other_tenant.json()["code"] == "record.not_found"
    assert other_tenant.json()["title"] == "Data tidak ditemukan"
