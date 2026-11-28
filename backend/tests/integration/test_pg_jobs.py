"""PostgresJobQueue/Runner pada skema `alembic upgrade head`, lewat role aplikasi (RLS aktif)."""

import asyncio
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.adapters.pg_jobs import PostgresJobQueue, PostgresJobRunner, redact_error
from app.core.ids import uuid7
from app.core.ports.jobs import HandlerRegistry, backoff_seconds
from app.core.tenancy.context import get_context, tenant_context
from app.modules.org.services import create_tenant
from tests.integration.conftest import APP_TEST_ROLE
from tests.integration.test_rls_meta import _alembic_config

Factory = async_sessionmaker[AsyncSession]
T0 = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(scope="session")
def jobs_table(database_url: str, rls_tables: None) -> Iterator[None]:
    def upgrade(connection: Connection) -> None:
        command.upgrade(_alembic_config(connection), "head")

    async def setup() -> None:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(upgrade)
                await conn.execute(text(f"GRANT SELECT ON tenants TO {APP_TEST_ROLE}"))
                await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE ON jobs TO {APP_TEST_ROLE}"))
        finally:
            await engine.dispose()

    asyncio.run(setup())
    yield


@pytest.fixture
def factory(session_factory: Factory, jobs_table: None) -> Factory:
    return session_factory


async def new_tenant(admin: AsyncEngine) -> UUID:
    async with AsyncSession(admin) as session:
        tenant = await create_tenant(session, name="T", slug=f"t-{uuid7().hex}")
        tenant_id = tenant.id
        await session.commit()
        return tenant_id


def make(
    factory: Factory, registry: HandlerRegistry, clock: Clock, **kw: Any
) -> tuple[PostgresJobQueue, PostgresJobRunner]:
    return PostgresJobQueue(factory, clock), PostgresJobRunner(factory, registry, clock, **kw)


async def row(admin: AsyncEngine, job_id: str) -> Any:
    async with admin.connect() as conn:
        return (
            (await conn.execute(text("SELECT * FROM jobs WHERE id = :i"), {"i": job_id}))
            .mappings()
            .one()
        )


async def test_enqueue_claim_success_with_tenant_context(
    factory: Factory, admin_engine: AsyncEngine
) -> None:
    tenant = await new_tenant(admin_engine)
    seen: list[tuple[UUID | None, dict[str, Any]]] = []
    reg, clock = HandlerRegistry(), Clock()

    @reg.register("t.ok")
    async def _ok(payload: dict[str, Any]) -> None:
        ctx = get_context()
        seen.append((ctx.tenant_id if ctx else None, payload))

    queue, runner = make(factory, reg, clock)
    with tenant_context(tenant):
        job_id = await queue.enqueue("t.ok", {"n": 1})
        assert await runner.run_pending() == 1
        assert await runner.run_pending() == 0
    assert seen == [(tenant, {"n": 1})]
    r = await row(admin_engine, job_id)
    assert (r["status"], r["attempts"], r["locked_by"], r["last_error"]) == (
        "succeeded",
        1,
        None,
        None,
    )


async def test_future_job_not_due(factory: Factory, admin_engine: AsyncEngine) -> None:
    tenant = await new_tenant(admin_engine)
    reg, clock = HandlerRegistry(), Clock()
    reg.register("t.ok")(lambda p: asyncio.sleep(0))
    queue, runner = make(factory, reg, clock)
    with tenant_context(tenant):
        await queue.enqueue("t.ok", {}, run_at=T0 + timedelta(minutes=5))
        assert await runner.run_pending() == 0
        clock.now = T0 + timedelta(minutes=6)
        assert await runner.run_pending() == 1


async def test_retry_with_backoff_then_failed(factory: Factory, admin_engine: AsyncEngine) -> None:
    tenant = await new_tenant(admin_engine)
    reg, clock = HandlerRegistry(), Clock()

    @reg.register("t.bad")
    async def _bad(payload: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    queue, runner = make(factory, reg, clock)
    with tenant_context(tenant):
        job_id = await queue.enqueue("t.bad", {}, max_attempts=3)
        for attempt in (1, 2):
            assert await runner.run_pending() == 1
            r = await row(admin_engine, job_id)
            assert (r["status"], r["attempts"]) == ("pending", attempt)
            assert r["run_at"] == clock.now + timedelta(seconds=backoff_seconds(attempt))
            assert await runner.run_pending() == 0  # belum jatuh tempo
            clock.now = r["run_at"]
        assert await runner.run_pending() == 1
    r = await row(admin_engine, job_id)
    assert (r["status"], r["attempts"], r["last_error"]) == ("failed", 3, "RuntimeError: boom")
    with tenant_context(tenant):
        clock.now += timedelta(days=1)
        assert await runner.run_pending() == 0


async def test_unknown_handler_is_recorded_as_failure(
    factory: Factory, admin_engine: AsyncEngine
) -> None:
    tenant = await new_tenant(admin_engine)
    queue, runner = make(factory, HandlerRegistry(), Clock())
    with tenant_context(tenant):
        job_id = await queue.enqueue("nope", {}, max_attempts=1)
        assert await runner.run_pending() == 1
    r = await row(admin_engine, job_id)
    assert r["status"] == "failed" and r["last_error"].startswith("KeyError")


async def test_idempotency_key(factory: Factory, admin_engine: AsyncEngine) -> None:
    a, b = await new_tenant(admin_engine), await new_tenant(admin_engine)
    queue, _ = make(factory, HandlerRegistry(), Clock())
    with tenant_context(a):
        first = await queue.enqueue("t.x", {"v": 1}, idempotency_key="k")
        assert await queue.enqueue("t.x", {"v": 2}, idempotency_key="k") == first
        assert await queue.enqueue("t.y", {}, idempotency_key="k") != first
        assert await queue.enqueue("t.x", {}) != await queue.enqueue("t.x", {})  # tanpa kunci
    with tenant_context(b):
        assert await queue.enqueue("t.x", {}, idempotency_key="k") != first
    assert (await row(admin_engine, first))["payload"] == {"v": 1}


async def test_tenant_cannot_see_or_claim_other_jobs(
    factory: Factory, admin_engine: AsyncEngine
) -> None:
    a, b = await new_tenant(admin_engine), await new_tenant(admin_engine)
    ran: list[UUID | None] = []
    reg, clock = HandlerRegistry(), Clock()

    @reg.register("t.ok")
    async def _ok(payload: dict[str, Any]) -> None:
        ctx = get_context()
        ran.append(ctx.tenant_id if ctx else None)

    queue, runner = make(factory, reg, clock)
    with tenant_context(a):
        job_a = await queue.enqueue("t.ok", {})
    with tenant_context(b):
        assert await runner.run_pending() == 0
        async with factory() as s:
            assert (await s.execute(text("SELECT count(*) FROM jobs"))).scalar_one() == 0
            await s.rollback()
        # Tulis lintas tenant ditolak RLS.
        async with factory() as s, s.begin():
            with pytest.raises(Exception, match="row-level security"):
                await s.execute(
                    text(
                        "INSERT INTO jobs (id, tenant_id, name, payload, max_attempts, run_at, "
                        "created_at, updated_at) VALUES (:i, :t, 'x', '{}', 1, now(), now(), now())"
                    ),
                    {"i": uuid7(), "t": a},
                )
    assert (await row(admin_engine, job_a))["status"] == "pending"
    assert await runner.run_tenants([b, a]) == 1  # run_tenants memasang konteks per tenant
    assert ran == [a]


async def test_lease_reclaim(factory: Factory, admin_engine: AsyncEngine) -> None:
    tenant = await new_tenant(admin_engine)
    reg, clock = HandlerRegistry(), Clock()
    calls: list[int] = []

    @reg.register("t.ok")
    async def _ok(payload: dict[str, Any]) -> None:
        calls.append(1)

    queue, _ = make(factory, reg, clock)
    runner1 = PostgresJobRunner(factory, reg, clock, lease_seconds=60, worker_id="w1")
    runner2 = PostgresJobRunner(factory, reg, clock, lease_seconds=60, worker_id="w2")
    with tenant_context(tenant):
        job_id = await queue.enqueue("t.ok", {}, max_attempts=2)
        claimed = await runner1._claim(10)  # worker w1 mati setelah klaim
        assert len(claimed) == 1
        assert await runner2.run_pending() == 0  # lease masih berlaku
        clock.now = T0 + timedelta(seconds=61)
        assert await runner2.run_pending() == 1
        await runner1._finish(claimed[0], RuntimeError("late"))  # w1 telat: tidak menimpa
    r = await row(admin_engine, job_id)
    assert (r["status"], r["attempts"], r["last_error"]) == ("succeeded", 2, None)
    assert calls == [1]


async def test_lease_expired_on_last_attempt_fails(
    factory: Factory, admin_engine: AsyncEngine
) -> None:
    tenant = await new_tenant(admin_engine)
    reg, clock = HandlerRegistry(), Clock()
    queue, _ = make(factory, reg, clock)
    runner = PostgresJobRunner(factory, reg, clock, lease_seconds=60)
    with tenant_context(tenant):
        job_id = await queue.enqueue("t.ok", {}, max_attempts=1)
        assert len(await runner._claim(10)) == 1
        clock.now = T0 + timedelta(seconds=61)
        assert await runner.run_pending() == 0
    r = await row(admin_engine, job_id)
    assert r["status"] == "failed" and r["last_error"].startswith("LeaseExpired")


async def test_last_error_redacted_and_truncated(
    factory: Factory, admin_engine: AsyncEngine
) -> None:
    tenant = await new_tenant(admin_engine)
    reg, clock = HandlerRegistry(), Clock()

    @reg.register("t.bad")
    async def _bad(payload: dict[str, Any]) -> None:
        raise ValueError("mail bob@example.com token=abc123 " + "x" * 2000)

    queue, runner = make(factory, reg, clock)
    with tenant_context(tenant):
        job_id = await queue.enqueue("t.bad", {})
        await runner.run_pending()
    err = (await row(admin_engine, job_id))["last_error"]
    assert len(err) <= 500 and err.startswith("ValueError: mail [email] token=[redacted]")
    assert "bob" not in err and "abc123" not in err


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Bearer abc.def password: hunter2 key AKIA" + "Z" * 30 + " a@b.co",
            "Bearer [redacted] password: [redacted] key [redacted] [email]",
        ),
        ("{'password': 'hunter2'}", "{'password': '[redacted]'}"),
        ('{"password": "hunter2"}', '{"password": "[redacted]"}'),
        ("password is hunter2", "password is [redacted]"),
        ("client_secret=abc", "client_secret=[redacted]"),
        ("db postgresql://user:pass@dbhost/x", "db postgresql://[dsn]@dbhost/x"),
        ("Authorization: Token abc123", "Authorization: [redacted]"),
        ("pwd: 'p@ss w0rd' end", "pwd: '[redacted]' end"),
        ('Secret = "top s3cret" end', 'Secret = "[redacted]" end'),
        ("call +62 812-3456-7890 now", "call [phone] now"),
        ("nik 3171234567890123 ok", "nik [nik] ok"),
        ("call +6281234567890 or 081234567890", "call [phone] or [phone]"),
        ("bad query [SQL: SELECT 1] [parameters: ('x@y.id',)]", "bad query "),
    ],
)
def test_redact_error_patterns(raw: str, expected: str) -> None:
    out = redact_error(RuntimeError(raw))
    assert out == f"RuntimeError: {expected}"
    leaks = ("hunter2", "abc123", "pass@", "3171234567890123", "81234567890", "w0rd", "s3cret")
    for leak in leaks:
        assert leak not in out


def test_redact_error_truncates_to_500() -> None:
    assert len(redact_error(RuntimeError("é" * 900))) == 500


@pytest.mark.parametrize(
    "evil",
    [
        "a." * 20000,
        "a" * 40000,
        "a@" + "b." * 20000,
        "password" * 5000,
        "0" * 40000,
        "://" * 13000,
        "x@" * 20000,
    ],
)
def test_redact_error_is_not_quadratic(evil: str) -> None:
    start = time.perf_counter()
    out = redact_error(RuntimeError(evil))
    assert time.perf_counter() - start < 0.05
    assert len(out) <= 500


async def test_enqueue_requires_tenant_context(factory: Factory, jobs_table: None) -> None:
    from app.core.tenancy.context import TenantContextMissing

    queue, runner = make(factory, HandlerRegistry(), Clock())
    with pytest.raises(TenantContextMissing):
        await queue.enqueue("t.ok", {})
    with pytest.raises(TenantContextMissing):
        await runner.run_pending()
    with pytest.raises(ValueError), tenant_context(uuid7()):
        await queue.enqueue("t.ok", {}, max_attempts=0)
