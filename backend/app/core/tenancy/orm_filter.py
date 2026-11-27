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
   (`TenantMismatchError`); flush `BaseRecord` tanpa konteks → `TenantContextMissing`.

Batas: statement Core / `text()` / objek `Table` tidak tercakup (tidak ada mapper) → lapis 2 (RLS)
dan aturan "kode bisnis hanya lewat repository".
"""

from typing import Any
from uuid import UUID

from sqlalchemy import event, inspect
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.core.tenancy.context import TenantContextMissing, get_context
from app.framework.record import BaseRecord


class TenantMismatchError(RuntimeError):
    """Objek `BaseRecord` milik tenant lain hendak ditulis di konteks tenant saat ini."""

    def __init__(self) -> None:
        super().__init__("record tenant_id does not match the active tenant context")


def _touches_records(state: ORMExecuteState) -> bool:
    return any(issubclass(m.class_, BaseRecord) for m in state.all_mappers)


def _tenant_criteria(tenant_id: UUID) -> Any:
    # Lambda mengacu ke `tenant_id` lewat closure → di-extract sebagai bound parameter.
    return with_loader_criteria(
        BaseRecord,
        lambda cls: cls.tenant_id == tenant_id,
        include_aliases=True,
    )


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_filter(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if not _touches_records(state):
        return
    ctx = get_context()
    if ctx is None:
        raise TenantContextMissing()
    state.statement = state.statement.options(_tenant_criteria(ctx.tenant_id))


def purge_foreign_records(session: Session, tenant_id: UUID | None) -> None:
    """Expunge instance `BaseRecord` di identity map yang bukan milik `tenant_id`."""
    for obj in list(session.identity_map.values()):
        if not isinstance(obj, BaseRecord):
            continue
        # `dict` (bukan getattr): instance expired tidak boleh memicu query di sini.
        owner = obj.__dict__.get("tenant_id")
        if owner is not None and owner != tenant_id:
            session.expunge(obj)


_original_get = Session.get


def _tenant_checked_get(self: Session, entity: Any, ident: Any, *args: Any, **kwargs: Any) -> Any:
    """`Session.get` yang menolak hit identity map milik tenant lain (identity map tak difilter)."""
    if not issubclass(inspect(entity).mapper.class_, BaseRecord):
        return _original_get(self, entity, ident, *args, **kwargs)
    ctx = get_context()
    if ctx is None:
        raise TenantContextMissing()
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
    for obj in records:
        owner = obj.__dict__.get("tenant_id")
        if owner is not None and owner != ctx.tenant_id:
            raise TenantMismatchError()
