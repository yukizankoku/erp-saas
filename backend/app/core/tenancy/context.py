"""Konteks tenant per request/job (ADR 0004 §2). Sengaja tanpa impor SQLAlchemy.

Satu `ContextVar` berisi `TenantContext` beku sehingga set/reset selalu atomik. Konteks diset
oleh dependency **`async def`** setelah autentikasi & resolusi tenant (dependency `def` berjalan
di threadpool sehingga contextvar tidak sampai ke endpoint). Kerja lintas tenant = loop dengan
satu `tenant_context(...)` dan satu transaksi per tenant; tidak ada mode "bypass".
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID


class TenantContextMissing(RuntimeError):
    """Operasi data tenant dijalankan tanpa konteks tenant (fail-closed)."""

    def __init__(self) -> None:
        super().__init__("tenant context is not set")


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: UUID
    user_id: UUID | None = None
    # Hanya untuk filter aplikasi `CompanyScoped` (P1-02); tidak dikirim ke database.
    company_id: UUID | None = None

    def __post_init__(self) -> None:
        # Validasi saat runtime juga: nilai ini dikirim ke `set_config` dan menjadi filter query.
        if not isinstance(self.tenant_id, UUID):
            raise TypeError("tenant_id must be a UUID")
        for name in ("user_id", "company_id"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, UUID):
                raise TypeError(f"{name} must be a UUID or None")


_current: ContextVar[TenantContext | None] = ContextVar("tenant_context", default=None)


def get_context() -> TenantContext | None:
    return _current.get()


def require_tenant_id() -> UUID:
    ctx = _current.get()
    if ctx is None:
        raise TenantContextMissing()
    return ctx.tenant_id


def current_user_id() -> UUID | None:
    ctx = _current.get()
    return ctx.user_id if ctx is not None else None


@contextmanager
def tenant_context(
    tenant_id: UUID,
    *,
    user_id: UUID | None = None,
    company_id: UUID | None = None,
) -> Iterator[TenantContext]:
    """Set konteks tenant selama blok `with`, lalu kembalikan nilai sebelumnya."""
    ctx = TenantContext(tenant_id=tenant_id, user_id=user_id, company_id=company_id)
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
