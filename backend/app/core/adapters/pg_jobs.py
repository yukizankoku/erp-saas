"""Job queue berbasis tabel Postgres (ADR 0005 bagian B). Tabel `jobs` = tabel sistem ber-RLS.

Bukan `BaseRecord` (tanpa soft delete/audit); tetap terikat tenant: RLS FORCE + filter eksplisit
`tenant_id` pada setiap query. Runner memakai role aplikasi dan bekerja per tenant (konteks tenant
aktif); tidak ada koneksi owner/BYPASSRLS. Enqueue memakai transaksi sendiri (belum outbox).
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    and_,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import uuid7
from app.core.ports.jobs import (
    FAILED,
    PENDING,
    SUCCEEDED,
    HandlerRegistry,
    backoff_seconds,
    job_handlers,
)
from app.core.tenancy.context import require_tenant_id, tenant_context
from app.core.tenancy.session import ensure_tenant_bound

RUNNING = "running"
MAX_ERROR_LENGTH = 500
DEFAULT_LEASE_SECONDS = 900

Clock = Callable[[], datetime]
SessionFactory = async_sessionmaker[AsyncSession]


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'running', 'succeeded', 'failed')", name="status"),
        Index("ix_jobs_status_run_at", "status", "run_at"),
        Index("ix_jobs_tenant_id_status_run_at", "tenant_id", "status", "run_at"),
        Index(
            "uq_jobs_tenant_id_name_idempotency_key",
            "tenant_id",
            "name",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        UniqueConstraint("tenant_id", "id", name="uq_jobs_tenant_id_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(String(MAX_ERROR_LENGTH))
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


_REDACTIONS = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+\S+"), r"\1 [redacted]"),
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)(\s*[=:]\s*)\S+"
        ),
        r"\1\2[redacted]",
    ),
    (re.compile(r"[A-Za-z0-9_\-+/=.]{24,}"), "[redacted]"),  # token/kunci panjang
)


def redact_error(exc: BaseException) -> str:
    """`NamaKelas: pesan` tanpa email/token, dipotong ke 500 karakter."""
    message = str(exc)
    for pattern, repl in _REDACTIONS:
        message = pattern.sub(repl, message)
    return f"{type(exc).__name__}: {message}"[:MAX_ERROR_LENGTH]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PostgresJobQueue:
    def __init__(self, session_factory: SessionFactory, clock: Clock = _utcnow) -> None:
        self._factory = session_factory
        self._clock = clock

    async def enqueue(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        run_at: datetime | None = None,
        max_attempts: int = 5,
        idempotency_key: str | None = None,
    ) -> str:
        """Simpan job untuk tenant konteks. Kunci idempotensi sama -> kembalikan id yang ada."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        tenant_id = require_tenant_id()
        now = self._clock()
        values = {
            "id": uuid7(),
            "tenant_id": tenant_id,
            "name": name,
            "payload": dict(payload),
            "status": PENDING,
            "attempts": 0,
            "max_attempts": max_attempts,
            "run_at": run_at or now,
            "idempotency_key": idempotency_key,
            "created_at": now,
            "updated_at": now,
        }
        async with self._factory() as session, session.begin():
            await ensure_tenant_bound(session)
            stmt = pg_insert(Job).values(**values)
            if idempotency_key is not None:
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["tenant_id", "name", "idempotency_key"],
                    index_where=text("idempotency_key IS NOT NULL"),
                )
            inserted = (await session.execute(stmt.returning(Job.id))).scalar_one_or_none()
            if inserted is None:
                inserted = (
                    await session.execute(
                        select(Job.id).where(
                            Job.tenant_id == tenant_id,
                            Job.name == name,
                            Job.idempotency_key == idempotency_key,
                        )
                    )
                ).scalar_one()
        return str(inserted)


@dataclass(frozen=True, slots=True)
class _Claimed:
    id: UUID
    name: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


class PostgresJobRunner:
    """Worker per tenant: `run_pending` bekerja untuk tenant konteks (role aplikasi, RLS aktif).

    Pekerja lintas tenant memanggil `run_tenants(ids)`; daftar tenant (mis. dari tabel `tenants`
    yang dapat dibaca role aplikasi) disediakan pemanggil, bukan runner.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        registry: HandlerRegistry | None = None,
        clock: Clock = _utcnow,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        worker_id: str | None = None,
        limit: int = 50,
    ) -> None:
        self._factory = session_factory
        self._registry = registry or job_handlers
        self._clock = clock
        self._lease = timedelta(seconds=lease_seconds)
        self._worker = worker_id or f"worker-{uuid4().hex[:12]}"
        self._limit = limit

    async def _claim(self, limit: int) -> list[_Claimed]:
        tenant_id = require_tenant_id()
        now = self._clock()
        due = or_(
            and_(Job.status == PENDING, Job.run_at <= now),
            and_(Job.status == RUNNING, Job.locked_at < now - self._lease),  # lease habis
        )
        claimed: list[_Claimed] = []
        async with self._factory() as session, session.begin():
            await ensure_tenant_bound(session)
            stmt = (
                select(Job)
                .where(Job.tenant_id == tenant_id, due)
                .order_by(Job.run_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            for job in (await session.execute(stmt)).scalars():
                job.updated_at = now
                if job.attempts >= job.max_attempts:  # worker sebelumnya mati di percobaan akhir
                    job.status = FAILED
                    job.last_error = "LeaseExpired: worker did not finish the last attempt"
                    continue
                job.status = RUNNING
                job.locked_at = now
                job.locked_by = self._worker
                job.attempts += 1
                claimed.append(
                    _Claimed(job.id, job.name, dict(job.payload), job.attempts, job.max_attempts)
                )
        return claimed

    async def _finish(self, job: _Claimed, error: BaseException | None) -> None:
        tenant_id = require_tenant_id()
        now = self._clock()
        async with self._factory() as session, session.begin():
            await ensure_tenant_bound(session)
            row = (
                await session.execute(
                    select(Job)
                    .where(
                        Job.tenant_id == tenant_id,
                        Job.id == job.id,
                        Job.status == RUNNING,
                        Job.locked_by == self._worker,  # lease diambil worker lain -> jangan timpa
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.updated_at = now
            row.locked_at = None
            row.locked_by = None
            if error is None:
                row.status = SUCCEEDED
                row.last_error = None
                return
            row.last_error = redact_error(error)
            if job.attempts >= job.max_attempts:
                row.status = FAILED
            else:
                row.status = PENDING
                row.run_at = now + timedelta(seconds=backoff_seconds(job.attempts))

    async def run_pending(self, limit: int | None = None) -> int:
        """Jalankan job jatuh tempo milik tenant konteks; kembalikan jumlah yang dijalankan."""
        claimed = await self._claim(limit or self._limit)
        for job in claimed:
            try:
                await self._registry.get(job.name)(dict(job.payload))
            except Exception as exc:  # dicatat di job (last_error), bukan ditelan
                await self._finish(job, exc)
            else:
                await self._finish(job, None)
        return len(claimed)

    async def run_tenants(self, tenant_ids: Iterable[UUID], limit: int | None = None) -> int:
        total = 0
        for tenant_id in tenant_ids:
            with tenant_context(tenant_id):
                total += await self.run_pending(limit)
        return total
