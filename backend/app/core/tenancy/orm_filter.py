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
   mereferensikan tabel `BaseRecord`. Penulisan ORM ke tabel `BaseRecord` = deny by default:
   INSERT tanpa `tenant_id` eksplisit (values/bindparam/params; default konteks satu-satunya
   sumber), tanpa ON CONFLICT, tanpa FROM SELECT, tanpa baris posisional; UPDATE/DELETE tanpa
   params list (bulk-by-PK) dan UPDATE tanpa `tenant_id` di SET. Upsert masa depan = helper
   repository.
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


_RECORD_TABLES: set[Table] = set()


def _remember_table(mapper: Any, class_: type) -> None:
    table = mapper.local_table
    if isinstance(table, Table):
        _RECORD_TABLES.add(table)


# Cache tabel `BaseRecord` (cocokkan lewat identitas objek Table): terisi saat kelas dipetakan.
event.listen(BaseRecord, "instrument_class", _remember_table, propagate=True)


def _seed_existing_tables() -> None:
    """Model yang sudah dipetakan sebelum modul ini diimpor."""
    stack = list(BaseRecord.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if hasattr(cls, "__mapper__"):
            _remember_table(cls.__mapper__, cls)


_seed_existing_tables()


def _references_records(state: ORMExecuteState) -> bool:
    if any(issubclass(m.class_, BaseRecord) for m in state.all_mappers):
        return True
    stmt: Any = state.statement
    return any(el in _RECORD_TABLES for el in visitors.iterate(stmt) if isinstance(el, Table))


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


def _check_write(state: ORMExecuteState) -> None:
    """Tolak-secara-default untuk penulisan ORM ke tabel `BaseRecord`.

    INSERT: `tenant_id` tidak boleh eksplisit di mana pun (values / bindparam / params); satu-
    satunya sumber adalah default kolom dari konteks. Tanpa ON CONFLICT, FROM SELECT, atau baris
    posisional. UPDATE/DELETE: tanpa params berupa list (bulk-by-PK); UPDATE tanpa `tenant_id`.
    """
    stmt: Any = state.statement
    params = state.parameters
    if state.is_insert:
        if (
            getattr(stmt, "select", None) is not None
            or getattr(stmt, "_post_values_clause", None) is not None
        ):
            raise TenantMismatchError()  # FROM SELECT / ON CONFLICT tidak bisa diverifikasi
    elif isinstance(params, list | tuple):
        raise TenantMismatchError()  # executemany / bulk update-by-PK
    rows = [params] if isinstance(params, dict) else list(params or [])
    if any(not isinstance(row, dict) or "tenant_id" in row for row in rows):
        raise TenantMismatchError()
    if _set_values(stmt):
        raise TenantMismatchError()


def _set_values(stmt: Any) -> list[str]:
    """Nama `tenant_id` pada `.values()` / `ordered_values()`; baris multi non-dict = terlarang.

    Memakai atribut internal `_values` / `_multi_values` / `_ordered_values` karena `compile()`
    gagal untuk INSERT ORM bulk; dijaga test regresi saat upgrade SQLAlchemy.
    """
    pairs: list[Any] = list((getattr(stmt, "_values", None) or {}).keys())
    pairs += [k for k, _ in getattr(stmt, "_ordered_values", None) or []]
    for group in getattr(stmt, "_multi_values", None) or ():
        for row in group:
            if not isinstance(row, dict):
                return ["positional"]
            pairs += list(row.keys())
    return [k for k in (getattr(k, "key", k) for k in pairs) if k == "tenant_id"]


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
    if (state.is_insert or state.is_update or state.is_delete) and _references_records(state):
        _check_write(state)
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
