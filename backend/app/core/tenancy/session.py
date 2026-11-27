"""Mengikat konteks tenant ke transaksi PostgreSQL (ADR 0004 §3).

Listener `after_begin` menjalankan `set_config('app.tenant_id', <tenant>, true)` di awal setiap
transaksi Session. `is_local = true` = semantik `SET LOCAL`: nilai hilang di akhir transaksi
sehingga tidak bocor antar klien di PgBouncer mode transaksi (Neon pooled). **Dilarang** `SET`
atau `set_config(..., false)` level sesi untuk `app.*`. Tanpa konteks → tidak di-set → policy RLS
membandingkan dengan NULL → 0 baris & INSERT/UPDATE ditolak (fail-closed).

Savepoint (`begin_nested`) tidak pernah mengikat atau memindah tenant: `set_config` di dalam
savepoint dibatalkan oleh `ROLLBACK TO SAVEPOINT` sedangkan `session.info` tidak, sehingga binding
dan GUC tidak lagi sinkron (query diam-diam 0 baris, atau berjalan sebagai tenant lain). Savepoint
mewarisi binding transaksi induk; konteks tenant yang berbeda → `TenantRebindError`.

Listener terdaftar global pada `Session` (Session sinkron di balik `AsyncSession`) saat modul ini
diimpor, begitu pula filter tenant ORM (`orm_filter`); `app.framework.repository` mengimpornya
sehingga setiap pemakai repository terlindungi.
"""

from uuid import UUID

from sqlalchemy import Connection, event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from app.core.tenancy.context import get_context, require_tenant_id
from app.core.tenancy.orm_filter import SESSION_INFO_KEY, TenantRebindError, purge_foreign_records

__all__ = ["SESSION_INFO_KEY", "SET_TENANT_SQL", "TenantRebindError", "ensure_tenant_bound"]

SET_TENANT_SQL = text("SELECT set_config('app.tenant_id', :tid, true)")


_SAVEPOINT_BIND = (
    "a savepoint cannot bind or switch the tenant of its transaction; "
    "bind the tenant before begin_nested()"
)


@event.listens_for(Session, "after_begin")
def _bind_tenant(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    ctx = get_context()
    if transaction.nested:
        # SAVEPOINT: GUC & info diwarisi dari transaksi induk dan tidak disentuh. Konteks tenant
        # lain (termasuk transaksi induk yang belum terikat) ditolak; savepoint dibatalkan oleh
        # pemanggil (`async with session.begin_nested()` → ROLLBACK TO SAVEPOINT).
        if ctx is not None and ctx.tenant_id != session.info.get(SESSION_INFO_KEY):
            raise TenantRebindError(_SAVEPOINT_BIND)
        return
    session.info[SESSION_INFO_KEY] = ctx.tenant_id if ctx is not None else None
    # Identity map tidak melewati filter ORM → buang instance tenant lain (ADR 0004 §7a).
    purge_foreign_records(session, ctx.tenant_id if ctx is not None else None)
    if ctx is not None:
        connection.execute(SET_TENANT_SQL, {"tid": str(ctx.tenant_id)})


@event.listens_for(Session, "after_transaction_end")
def _clear_binding(session: Session, transaction: SessionTransaction) -> None:
    # Binding hanya berlaku selama transaksi; sisa nilai lama membuat transaksi autobegin berikutnya
    # (belum punya koneksi) tampak terikat ke tenant sebelumnya.
    if transaction.parent is None:
        session.info.pop(SESSION_INFO_KEY, None)


async def ensure_tenant_bound(session: AsyncSession) -> UUID:
    """Pastikan transaksi aktif terikat ke tenant konteks saat ini; kembalikan tenant_id.

    Menangani transaksi yang dimulai sebelum konteks diset (mis. lookup autentikasi): tanpa
    pengikatan ulang, query pertama sesudahnya diam-diam mengembalikan 0 baris. Berpindah dari
    satu tenant ke tenant lain di transaksi yang sama ditolak (satu transaksi per tenant), begitu
    pula pengikatan pertama di dalam savepoint (akan batal bersama `ROLLBACK TO SAVEPOINT`).
    """
    tenant_id = require_tenant_id()
    await session.connection()  # memulai transaksi/savepoint bila belum → after_begin berjalan
    bound = session.info.get(SESSION_INFO_KEY)
    if bound == tenant_id:
        return tenant_id
    if bound is not None:
        raise TenantRebindError()
    if session.in_nested_transaction():
        raise TenantRebindError(_SAVEPOINT_BIND)
    await session.execute(SET_TENANT_SQL, {"tid": str(tenant_id)})
    session.info[SESSION_INFO_KEY] = tenant_id
    purge_foreign_records(session.sync_session, tenant_id)
    return tenant_id
