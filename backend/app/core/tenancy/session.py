"""Mengikat konteks tenant ke transaksi PostgreSQL (ADR 0004 §3).

Listener `after_begin` menjalankan `set_config('app.tenant_id', <tenant>, true)` di awal setiap
transaksi Session. `is_local = true` = semantik `SET LOCAL`: nilai hilang di akhir transaksi
sehingga tidak bocor antar klien di PgBouncer mode transaksi (Neon pooled). **Dilarang** `SET`
atau `set_config(..., false)` level sesi untuk `app.*`. Tanpa konteks → tidak di-set → policy RLS
membandingkan dengan NULL → 0 baris & INSERT/UPDATE ditolak (fail-closed).

Listener terdaftar global pada `Session` (Session sinkron di balik `AsyncSession`) saat modul ini
diimpor; `app.framework.repository` mengimpornya sehingga setiap pemakai repository terlindungi.
"""

from uuid import UUID

from sqlalchemy import Connection, event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from app.core.tenancy.context import get_context, require_tenant_id

SESSION_INFO_KEY = "rls_tenant_id"
SET_TENANT_SQL = text("SELECT set_config('app.tenant_id', :tid, true)")


class TenantRebindError(RuntimeError):
    """Transaksi yang sudah terikat ke satu tenant dipakai untuk tenant lain."""

    def __init__(self) -> None:
        super().__init__(
            "transaction is already bound to another tenant; commit/rollback before switching"
        )


@event.listens_for(Session, "after_begin")
def _bind_tenant(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    ctx = get_context()
    session.info[SESSION_INFO_KEY] = ctx.tenant_id if ctx is not None else None
    if ctx is not None:
        connection.execute(SET_TENANT_SQL, {"tid": str(ctx.tenant_id)})


async def ensure_tenant_bound(session: AsyncSession) -> UUID:
    """Pastikan transaksi aktif terikat ke tenant konteks saat ini; kembalikan tenant_id.

    Menangani transaksi yang dimulai sebelum konteks diset (mis. lookup autentikasi): tanpa
    pengikatan ulang, query pertama sesudahnya diam-diam mengembalikan 0 baris. Berpindah dari
    satu tenant ke tenant lain di transaksi yang sama ditolak (satu transaksi per tenant).
    """
    tenant_id = require_tenant_id()
    await session.connection()  # memulai transaksi bila belum → after_begin berjalan
    bound = session.info.get(SESSION_INFO_KEY)
    if bound == tenant_id:
        return tenant_id
    if bound is not None:
        raise TenantRebindError()
    await session.execute(SET_TENANT_SQL, {"tid": str(tenant_id)})
    session.info[SESSION_INFO_KEY] = tenant_id
    return tenant_id
