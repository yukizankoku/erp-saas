"""Filter tenant otomatis di level ORM — lapis 1 isolasi tenant (ADR 0004 §7a, PRD §6.4).

Terdaftar global pada `Session` saat modul diimpor (`app.core.tenancy.session` mengimpornya).

1. `do_orm_execute`: setiap SELECT / bulk UPDATE / bulk DELETE ORM yang menyentuh mapper
   `BaseRecord` mendapat `with_loader_criteria(BaseRecord, tenant_id = <konteks>)` (termasuk alias,
   lazy load, selectin, dan refresh kolom). Nilai tenant ada di closure lambda sehingga menjadi
   bound parameter (cache kompilasi dipakai bersama, nilai per eksekusi). Tanpa konteks →
   `TenantContextMissing` (fail-closed, bukan hasil kosong diam-diam). Tidak ada opsi bypass.
2. Identity map: `session.get()` tidak mengeksekusi query bila objek sudah ada di identity map,
   jadi listener 1 tidak terpanggil (dan `after_begin` pun tidak, transaksi belum tentu mulai).
   Dua lapis: (a) `Session.get` dibungkus (`_tenant_checked_get`): tanpa konteks → error, hit milik
   tenant lain → expunge + `None`; (b) `purge_foreign_records()` (dipanggil di awal transaksi
   non-nested dan saat `ensure_tenant_bound`) men-expunge instance `BaseRecord` yang `tenant_id`-nya
   ≠ tenant terikat (atau semua, bila tanpa konteks) agar tidak lolos lewat jalur lain. Instance
   expired tidak diketahui tenant-nya tanpa query, tetapi refresh-nya melewati listener 1 → baris
   tenant lain = `ObjectDeletedError`/None.
3. `before_flush`: objek baru/berubah/dihapus dengan `tenant_id` ≠ konteks ditolak
   (`TenantMismatchError`); objek persistent yang `tenant_id`-nya tidak termuat (expired) atau
   diubah (history) ditolak; flush `BaseRecord` tanpa konteks → `TenantContextMissing`.
4. Kriteria dipasang pada SETIAP SELECT/UPDATE/DELETE ORM bila ada konteks (subquery, EXISTS,
   UNION, `count()` tidak terlihat oleh `all_mappers`). Tanpa konteks → error hanya bila statement
   mereferensikan tabel milik `BaseRecord`. INSERT/UPDATE ke `BaseRecord`: `tenant_id` eksplisit ≠
   konteks ditolak, `tenant_id` di SET UPDATE ditolak, `INSERT ... FROM SELECT` ditolak.
5. Transaksi yang sudah terikat ke tenant lain dan dipakai di konteks berbeda → `TenantRebindError`
   (execute, get, flush; termasuk `merge`).

Batas: statement Core / `text()` / objek `Table` tidak tercakup → lapis 2 (RLS) dan aturan "kode
bisnis hanya lewat repository". `session.query(...).get()` (legacy) melewati pembungkus `get` →
`session.query` dilarang di kode bisnis.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import Table, event, inspect
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria
from sqlalchemy.sql import visitors

from app.core.tenancy.context import TenantContextMissing, get_context
from app.framework.record import BaseRecord

SESSION_INFO_KEY = "rls_tenant_id"
_ALREADY_BOUND = "transaction is already bound to another tenant; commit/rollback before switching"


class TenantRebindError(RuntimeError):
    """Transaksi yang sudah terikat ke satu tenant dipakai untuk tenant lain, atau tenant
    hendak diikat/dipindah dari dalam savepoint."""

    def __init__(self, message: str = _ALREADY_BOUND) -> None:
        super().__init__(message)


class TenantMismatchError(RuntimeError):
    """Objek/statement `BaseRecord` milik tenant lain hendak ditulis di konteks tenant saat ini."""

    def __init__(self) -> None:
        super().__init__("record tenant_id does not match the active tenant context")


def _record_classes() -> list[type[BaseRecord]]:
    found: list[type[BaseRecord]] = []
    stack = list(BaseRecord.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if "__mapper__" in cls.__dict__ or hasattr(cls, "__mapper__"):
            found.append(cls)
    return found


def _references_records(state: ORMExecuteState) -> bool:
    if any(issubclass(m.class_, BaseRecord) for m in state.all_mappers):
        return True
    names = {
        t.name
        for cls in _record_classes()
        for t in [inspect(cls).local_table]
        if isinstance(t, Table)
    }
    stmt: Any = state.statement
    return any(isinstance(el, Table) and el.name in names for el in visitors.iterate(stmt))


def _check_not_rebound(session: Session, tenant_id: UUID) -> None:
    bound = session.info.get(SESSION_INFO_KEY)
    if session.in_transaction() and bound is not None and bound != tenant_id:
        raise TenantRebindError()


def _tenant_criteria(tenant_id: UUID) -> Any:
    # Lambda mengacu ke `tenant_id` lewat closure → di-extract sebagai bound parameter.
    return with_loader_criteria(
        BaseRecord,
        lambda cls: cls.tenant_id == tenant_id,
        include_aliases=True,
    )


def _check_write(state: ORMExecuteState, tenant_id: UUID) -> None:
    stmt = state.statement
    if state.is_insert and getattr(stmt, "select", None) is not None:
        raise TenantMismatchError()  # INSERT ... FROM SELECT tidak bisa diverifikasi
    params = state.parameters
    batches = [params] if isinstance(params, dict) else list(params or [])
    for row in batches:
        if "tenant_id" in row and row["tenant_id"] != tenant_id:  # type: ignore[index]
            raise TenantMismatchError()
    for _key, value in _set_values(stmt):
        # INSERT: hanya literal = tenant konteks; UPDATE: tidak boleh men-SET tenant_id.
        if state.is_update or getattr(value, "value", None) != tenant_id:
            raise TenantMismatchError()
    if state.is_update and any("tenant_id" in row for row in batches):
        raise TenantMismatchError()


def _set_values(stmt: Any) -> list[tuple[str, Any]]:
    """Pasangan (nama kolom, nilai) untuk `tenant_id` pada `.values()` / `ordered_values()`.

    Memakai atribut internal `_values` / `_multi_values` / `_ordered_values` karena `compile()`
    gagal untuk INSERT ORM bulk; dijaga test regresi saat upgrade SQLAlchemy.
    """
    pairs: list[tuple[Any, Any]] = list((getattr(stmt, "_values", None) or {}).items())
    pairs += list(getattr(stmt, "_ordered_values", None) or [])
    for group in getattr(stmt, "_multi_values", None) or ():
        for row in group:
            pairs += list(row.items()) if isinstance(row, dict) else []
    return [(k, v) for k, v in ((getattr(k, "key", k), v) for k, v in pairs) if k == "tenant_id"]


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_filter(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_insert or state.is_update or state.is_delete):
        return
    ctx = get_context()
    if ctx is None:
        if _references_records(state):
            raise TenantContextMissing()
        return
    _check_not_rebound(state.session, ctx.tenant_id)
    if (state.is_insert or state.is_update) and _references_records(state):
        _check_write(state, ctx.tenant_id)
    if not state.is_insert:
        # Tanpa syarat `all_mappers`: subquery/EXISTS/UNION/count() tak terlihat di sana.
        state.statement = state.statement.options(_tenant_criteria(ctx.tenant_id))


def purge_foreign_records(session: Session, tenant_id: UUID | None) -> None:
    """Expunge instance `BaseRecord` di identity map yang bukan milik `tenant_id`."""
    for obj in list(session.identity_map.values()):
        if not isinstance(obj, BaseRecord):
            continue
        # `dict` (bukan getattr): instance expired tidak boleh memicu query di sini.
        owner = obj.__dict__.get("tenant_id")
        # tenant_id tidak termuat (expired) = dianggap asing; akan dimuat ulang lewat filter.
        if owner is None or owner != tenant_id:
            session.expunge(obj)


_original_get = Session.get


def _tenant_checked_get(self: Session, entity: Any, ident: Any, *args: Any, **kwargs: Any) -> Any:
    """`Session.get` yang menolak hit identity map milik tenant lain (identity map tak difilter)."""
    if not issubclass(inspect(entity).mapper.class_, BaseRecord):
        return _original_get(self, entity, ident, *args, **kwargs)
    ctx = get_context()
    if ctx is None:
        raise TenantContextMissing()
    _check_not_rebound(self, ctx.tenant_id)
    obj = _original_get(self, entity, ident, *args, **kwargs)
    if obj is not None and obj.__dict__.get("tenant_id", ctx.tenant_id) != ctx.tenant_id:
        self.expunge(obj)
        return None
    return obj


Session.get = _tenant_checked_get  # type: ignore[method-assign]


@event.listens_for(Session, "before_flush")
def _check_flush(session: Session, flush_context: Any, instances: Any) -> None:
    records = [
        obj
        for group in (session.new, session.dirty, session.deleted)
        for obj in group
        if isinstance(obj, BaseRecord)
    ]
    if not records:
        return
    ctx = get_context()
    if ctx is None:
        raise TenantContextMissing()
    _check_not_rebound(session, ctx.tenant_id)
    for obj in records:
        owner = obj.__dict__.get("tenant_id")
        if owner is not None and owner != ctx.tenant_id:
            raise TenantMismatchError()
        if obj in session.new:
            continue
        # Persistent: tenant_id tak termuat (expired) atau diubah → tolak.
        if owner is None or inspect(obj).attrs.tenant_id.history.has_changes():
            raise TenantMismatchError()
