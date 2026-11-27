"""Filter tenant ORM (lapis 1) berdiri sendiri: sesi di `admin_engine`
(superuser → RLS TIDAK berlaku).

Semua test di sini membuktikan `app.core.tenancy.orm_filter` (ADR 0004 §7a) tanpa bantuan RLS.
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.ids import uuid7
from app.core.tenancy.context import TenantContextMissing, tenant_context
from app.core.tenancy.orm_filter import TenantMismatchError
from app.core.tenancy.session import TenantRebindError
from tests.integration.models import Item

pytestmark = pytest.mark.usefixtures("rls_tables")


@pytest.fixture
def factory(admin_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(admin_engine, expire_on_commit=False)


async def _seed_item(engine: AsyncEngine, tenant: UUID, name: str = "seed") -> UUID:
    row_id = uuid7()
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO rls_test_items (id, tenant_id, name) VALUES (:id, :t, :n)"),
            {"id": row_id, "t": tenant, "n": name},
        )
    return row_id


async def _raw_name(engine: AsyncEngine, row_id: UUID) -> str | None:
    async with engine.connect() as conn:
        return (
            await conn.execute(text("SELECT name FROM rls_test_items WHERE id = :i"), {"i": row_id})
        ).scalar_one_or_none()


async def test_admin_session_really_bypasses_rls(admin_engine: AsyncEngine) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    row_b = await _seed_item(admin_engine, tenant_b)
    async with admin_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant_a)}
        )
        found = (
            await conn.execute(text("SELECT id FROM rls_test_items WHERE id = :i"), {"i": row_b})
        ).scalar_one_or_none()
    assert found == row_b  # tenant A terikat di GUC, tetapi superuser tetap melihat baris B


async def test_get_other_tenant_fresh_session(
    admin_engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    row_b = await _seed_item(admin_engine, tenant_b)
    row_a = await _seed_item(admin_engine, tenant_a)
    async with factory() as session:
        with tenant_context(tenant_a):
            assert await session.get(Item, row_b) is None
            own = await session.get(Item, row_a)
            assert own is not None and own.tenant_id == tenant_a


async def test_get_other_tenant_from_identity_map(
    admin_engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    row_b = await _seed_item(admin_engine, tenant_b)
    async with factory() as session:
        with tenant_context(tenant_b):
            loaded = await session.get(Item, row_b)
            assert loaded is not None
            await session.commit()  # expire_on_commit=False → objek B tetap di identity map
        assert row_b in {o.id for o in session.identity_map.values()}
        with tenant_context(tenant_a):
            assert await session.get(Item, row_b) is None


async def test_expired_other_tenant_instance_is_not_returned(
    admin_engine: AsyncEngine,
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    row_b = await _seed_item(admin_engine, tenant_b)
    async with async_sessionmaker(admin_engine, expire_on_commit=True)() as session:
        with tenant_context(tenant_b):
            assert await session.get(Item, row_b) is not None
            await session.commit()  # instance expired: tenant_id tak diketahui tanpa query
        with tenant_context(tenant_a):
            assert await session.get(Item, row_b) is None


async def test_bulk_update_and_delete_do_not_touch_other_tenant(
    admin_engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    row_a = await _seed_item(admin_engine, tenant_a, "a")
    row_b = await _seed_item(admin_engine, tenant_b, "b")
    async with factory() as session:
        with tenant_context(tenant_a):
            result = await session.execute(sa.update(Item).values(name="changed"))
            assert result.rowcount == 1  # type: ignore[attr-defined]
            await session.commit()
    assert await _raw_name(admin_engine, row_a) == "changed"
    assert await _raw_name(admin_engine, row_b) == "b"
    async with factory() as session:
        with tenant_context(tenant_a):
            await session.execute(sa.delete(Item).where(Item.id.in_([row_a, row_b])))
            await session.commit()
    assert await _raw_name(admin_engine, row_a) is None
    assert await _raw_name(admin_engine, row_b) == "b"


async def _seed_cross_children(
    engine: AsyncEngine, tenant_a: UUID, tenant_b: UUID
) -> tuple[UUID, UUID, UUID]:
    parent = await _seed_item(engine, tenant_a, "parent")
    child_a, child_b = uuid7(), uuid7()
    async with engine.begin() as conn:
        for cid, tid, label in ((child_a, tenant_a, "own"), (child_b, tenant_b, "foreign")):
            await conn.execute(
                text(
                    "INSERT INTO rls_test_item_children (id, tenant_id, item_id, label) "
                    "VALUES (:id, :t, :p, :l)"
                ),
                {"id": cid, "t": tid, "p": parent, "l": label},
            )
    return parent, child_a, child_b


async def test_lazy_and_selectin_load_exclude_other_tenant(
    admin_engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    parent, child_a, _ = await _seed_cross_children(admin_engine, tenant_a, tenant_b)
    async with factory() as session:
        with tenant_context(tenant_a):
            item = (
                await session.execute(
                    sa.select(Item).where(Item.id == parent).options(selectinload(Item.children))  # type: ignore[attr-defined]
                )
            ).scalar_one()
            assert [c.id for c in item.children] == [child_a]  # type: ignore[attr-defined]
    async with factory() as session:
        with tenant_context(tenant_a):
            item = (await session.execute(sa.select(Item).where(Item.id == parent))).scalar_one()
            lazy = await session.run_sync(lambda s: list(item.children))  # type: ignore[attr-defined]
            assert [c.id for c in lazy] == [child_a]


async def test_select_without_context_raises(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        with pytest.raises(TenantContextMissing):
            await session.execute(sa.select(Item))
        with pytest.raises(TenantContextMissing):
            await session.get(Item, uuid7())


async def test_non_record_statements_are_untouched(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1


async def test_flush_foreign_tenant_object_rejected(
    admin_engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    row_id = uuid7()
    async with factory() as session:
        with tenant_context(tenant_a):
            session.add(Item(id=row_id, tenant_id=tenant_b, name="evil"))
            with pytest.raises(TenantMismatchError):
                await session.flush()
            await session.rollback()
    assert await _raw_name(admin_engine, row_id) is None


async def test_flush_without_context_rejected(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        session.add(Item(id=uuid7(), tenant_id=uuid7(), name="x"))
        with pytest.raises(TenantContextMissing):
            await session.flush()
        await session.rollback()


async def test_new_object_without_tenant_id_gets_context_tenant(
    admin_engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant_a = uuid7()
    row_id = uuid7()
    async with factory() as session:
        with tenant_context(tenant_a):
            item = Item(id=row_id, name="ok")
            session.add(item)
            await session.commit()
            assert item.tenant_id == tenant_a
    assert await _raw_name(admin_engine, row_id) == "ok"


async def test_same_statement_object_per_tenant(
    admin_engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
) -> None:
    """Satu objek statement dieksekusi A → B → A lewat sesi nyata: nilai tenant tidak ter-cache."""
    tenant_a, tenant_b = uuid7(), uuid7()
    row_a = await _seed_item(admin_engine, tenant_a)
    row_b = await _seed_item(admin_engine, tenant_b)
    stmt = sa.select(Item.id).where(Item.id.in_([row_a, row_b]))
    for tenant, expected in ((tenant_a, row_a), (tenant_b, row_b), (tenant_a, row_a)):
        async with factory() as session:
            with tenant_context(tenant):
                assert (await session.execute(stmt)).scalars().all() == [expected]


Builder = Callable[[UUID], Any]
LEAKY: dict[str, Builder] = {
    "count": lambda rb: sa.select(sa.func.count()).select_from(Item),
    "exists": lambda rb: sa.select(sa.exists().where(Item.id == rb)),
    "scalar_subquery": lambda rb: sa.select(
        sa.select(Item.name).where(Item.id == rb).scalar_subquery()
    ),
    "union_all": lambda rb: sa.union_all(
        sa.select(Item.id).where(Item.id == rb), sa.select(Item.id).where(Item.id == rb)
    ),
}


@pytest.mark.parametrize("name", list(LEAKY))
async def test_nested_entities_are_filtered(
    name: str, admin_engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    row_b = await _seed_item(admin_engine, tenant_b, "secret")
    async with factory() as session:
        with tenant_context(tenant_a):
            rows = (await session.execute(LEAKY[name](row_b))).all()
        assert rows in ([(0,)], [(False,)], [(None,)], []), rows
        with pytest.raises(TenantContextMissing):
            await session.execute(LEAKY[name](row_b))


async def test_expired_foreign_instance_cannot_be_modified_or_deleted(
    admin_engine: AsyncEngine,
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    row_b = await _seed_item(admin_engine, tenant_b, "victim")
    maker = async_sessionmaker(admin_engine, expire_on_commit=True)
    for mutate in ("update", "delete"):
        async with maker() as session:
            with tenant_context(tenant_b):
                obj = await session.get(Item, row_b)
                assert obj is not None
                await session.commit()
            with tenant_context(tenant_a):
                if mutate == "update":
                    obj.name = "pwned"
                else:
                    await session.delete(obj)
                with pytest.raises(TenantMismatchError):
                    await session.commit()
                await session.rollback()
        assert await _raw_name(admin_engine, row_b) == "victim"


async def test_retargeting_tenant_id_and_same_txn_switch_rejected(
    admin_engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    row_b = await _seed_item(admin_engine, tenant_b, "victim")
    async with factory() as session:
        with tenant_context(tenant_b):
            obj = await session.get(Item, row_b)
            assert obj is not None
        with tenant_context(tenant_a):
            obj.tenant_id = tenant_a
            obj.name = "stolen"
            with pytest.raises(TenantRebindError):
                await session.commit()
            await session.rollback()
    async with factory() as session:
        with tenant_context(tenant_b):
            obj = await session.get(Item, row_b)
            assert obj is not None
            await session.commit()
        with tenant_context(tenant_b):
            obj.tenant_id = tenant_a  # ubah tenant_id persistent walau konteks sama
            with pytest.raises(TenantMismatchError):
                await session.flush()
            await session.rollback()
    async with factory() as session:
        with tenant_context(tenant_b):
            with_b = await session.get(Item, row_b)
            assert with_b is not None and with_b.name == "victim"


async def test_same_txn_context_switch_blocks_get_execute_merge(
    admin_engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    row_b = await _seed_item(admin_engine, tenant_b)
    async with factory() as session:
        with tenant_context(tenant_b):
            await session.get(Item, row_b)
        with tenant_context(tenant_a):
            with pytest.raises(TenantRebindError):
                await session.get(Item, row_b)
            with pytest.raises(TenantRebindError):
                await session.execute(sa.select(Item))
            with pytest.raises(TenantRebindError):
                await session.merge(Item(id=row_b, name="m"))


async def _count_rows(engine: AsyncEngine, tenant: UUID) -> int:
    async with engine.connect() as conn:
        return int(
            (
                await conn.execute(
                    text("SELECT count(*) FROM rls_test_items WHERE tenant_id = :t"), {"t": tenant}
                )
            ).scalar_one()
        )


async def test_insert_and_update_cannot_target_other_tenant(
    admin_engine: AsyncEngine, factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant_a, tenant_b = uuid7(), uuid7()
    row_b = await _seed_item(admin_engine, tenant_b, "secret")
    row_a = await _seed_item(admin_engine, tenant_a, "mine")
    from_select = sa.insert(Item).from_select(
        ["id", "tenant_id", "name"],
        sa.select(sa.literal(uuid7()), sa.literal(tenant_a), Item.name).where(Item.id == row_b),
    )
    statements: list[tuple[Any, Any]] = [
        (sa.insert(Item).values(id=uuid7(), tenant_id=tenant_b, name="x"), None),
        (sa.insert(Item), [{"id": uuid7(), "tenant_id": tenant_b, "name": "x"}]),
        (from_select, None),
        (sa.update(Item).where(Item.id == row_a).values(tenant_id=tenant_b), None),
        (sa.update(Item).where(Item.id == row_a).values(tenant_id=tenant_a), None),
    ]
    for stmt, params in statements:
        async with factory() as session:
            with tenant_context(tenant_a):
                with pytest.raises(TenantMismatchError):
                    await session.execute(stmt, params)
                await session.rollback()
    assert await _count_rows(admin_engine, tenant_b) == 1
    assert await _count_rows(admin_engine, tenant_a) == 1
    async with factory() as session:  # insert sah (tenant sendiri / default) tetap jalan
        with tenant_context(tenant_a):
            await session.execute(sa.insert(Item).values(id=uuid7(), tenant_id=tenant_a, name="ok"))
            await session.execute(sa.insert(Item), [{"id": uuid7(), "name": "ok2"}])
            await session.commit()
    assert await _count_rows(admin_engine, tenant_a) == 3


async def test_insert_without_context_rejected(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        with pytest.raises(TenantContextMissing):
            await session.execute(sa.insert(Item).values(id=uuid7(), tenant_id=uuid7(), name="x"))
