from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from app.core.adapters.jobs import MemoryJobQueue
from app.core.adapters.mailer import ConsoleMailer, MemoryMailer
from app.core.adapters.pdf import UnavailablePdfRenderer
from app.core.adapters.realtime import MemoryRealtime
from app.core.adapters.storage import LocalFileStorage, MemoryStorage, UrlSigner
from app.core.config import Settings
from app.core.ids import uuid7
from app.core.ports import (
    EmailMessage,
    JobQueue,
    JobRunner,
    Mailer,
    ObjectStorage,
    PdfRenderer,
    Realtime,
    tenant_key,
)
from app.core.ports.jobs import HandlerRegistry, backoff_seconds
from app.core.ports.providers import UnknownBackendError, build_storage, validate_backends
from app.core.ports.storage import InvalidStorageKey, validate_key
from app.core.tenancy.context import TenantContextMissing, tenant_context

SECRET = "test-secret-not-real"  # noqa: S105
T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def _q(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_protocol_conformance(tmp_path: Path) -> None:
    assert isinstance(MemoryStorage(), ObjectStorage)
    assert isinstance(LocalFileStorage(tmp_path), ObjectStorage)
    assert isinstance(ConsoleMailer(), Mailer) and isinstance(MemoryMailer(), Mailer)
    assert isinstance(MemoryJobQueue(), JobQueue) and isinstance(MemoryJobQueue(), JobRunner)
    assert isinstance(MemoryRealtime(), Realtime)
    assert isinstance(UnavailablePdfRenderer(), PdfRenderer)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/etc/passwd",
        "../x",
        "a/../b",
        "a/./b",
        "a//b",
        "a/",
        "a\\b",
        "a/b\x00",
        "C:/x",
        "a/ /b",
    ],
)
def test_validate_key_rejects_unsafe(key: str) -> None:
    with pytest.raises(InvalidStorageKey):
        validate_key(key)


def test_tenant_key_uses_context_and_rejects_traversal() -> None:
    tid = uuid7()
    with tenant_context(tid):
        assert tenant_key("att", "f.pdf") == f"t/{tid}/att/f.pdf"
        with pytest.raises(InvalidStorageKey):
            tenant_key("..", "other")
        with pytest.raises(InvalidStorageKey):
            tenant_key("a", "")
    with pytest.raises(TenantContextMissing):
        tenant_key("x")


async def test_local_storage_round_trip_and_traversal(tmp_path: Path) -> None:
    st = LocalFileStorage(tmp_path / "root")
    key = tenant_key("a", "b.txt", tenant_id=uuid7())
    assert not await st.exists(key)
    await st.put(key, b"hi", "text/plain")
    assert await st.exists(key) and await st.get(key) == b"hi"
    await st.delete(key)
    assert not await st.exists(key)
    for bad in ("../outside", "/abs", "a/../../x"):
        with pytest.raises(InvalidStorageKey):
            await st.put(bad, b"x", "text/plain")
    assert not (tmp_path / "outside").exists()


async def test_memory_storage_round_trip() -> None:
    st = MemoryStorage()
    await st.put("t/x/a", b"1", "text/plain")
    assert await st.get("t/x/a") == b"1"
    await st.delete("t/x/a")
    with pytest.raises(FileNotFoundError):
        await st.get("t/x/a")


async def test_presigned_verify_expired_and_tampered() -> None:
    clock = FakeClock()
    signer = UrlSigner(SECRET, clock)
    st = MemoryStorage(signer)
    up = await st.presigned_upload_url("t/x/f.png", "image/png", 1000, 60)
    assert up.method == "PUT" and up.headers["Content-Type"] == "image/png"
    assert up.expires_at == T0 + timedelta(seconds=60)
    q = _q(up.url)
    args = ("PUT", "t/x/f.png", int(q["exp"]), q["sig"], "image/png", 1000)
    assert signer.verify_signed(*args)
    # dirusak: key, method, content type, max_bytes, exp, sig
    assert not signer.verify_signed("PUT", "t/x/g.png", *args[2:])
    assert not signer.verify_signed("GET", "t/x/f.png", *args[2:])
    assert not signer.verify_signed(*args[:4], "text/html", 1000)
    assert not signer.verify_signed(*args[:4], "image/png", 10**9)
    assert not signer.verify_signed(
        "PUT", "t/x/f.png", int(q["exp"]) + 999, q["sig"], "image/png", 1000
    )
    assert not signer.verify_signed(*args[:3], "0" * 64, "image/png", 1000)
    assert not UrlSigner("other-secret", clock).verify_signed(*args)
    assert not signer.verify_signed("PUT", "../x", *args[2:])
    clock.now = T0 + timedelta(seconds=61)
    assert not signer.verify_signed(*args)
    down = await st.presigned_download_url("t/x/f.png", 30)
    dq = _q(down.url)
    clock.now = T0 + timedelta(seconds=10)
    assert signer.verify_signed("GET", "t/x/f.png", int(dq["exp"]), dq["sig"])


async def test_presign_rejects_bad_key_and_expiry() -> None:
    st = MemoryStorage(UrlSigner(SECRET))
    with pytest.raises(InvalidStorageKey):
        await st.presigned_download_url("../x", 10)
    with pytest.raises(ValueError):
        await st.presigned_download_url("t/x/a", 0)


async def test_mailer_capture() -> None:
    m = MemoryMailer()
    msg = EmailMessage(to=("a@example.com",), subject="Hi", text="body")
    await m.send(msg)
    assert m.sent == [msg] and msg.html is None and msg.reply_to is None
    await ConsoleMailer().send(msg)


async def test_job_success() -> None:
    reg, seen = HandlerRegistry(), []

    @reg.register("ok")
    async def ok(payload: dict[str, Any]) -> None:
        seen.append(payload)

    q = MemoryJobQueue(reg, FakeClock())
    jid = await q.enqueue("ok", {"n": 1})
    assert await q.run_pending() == 1
    assert seen == [{"n": 1}] and q.jobs[jid].status == "succeeded"
    assert await q.run_pending() == 0


async def test_job_run_at_in_future_waits() -> None:
    reg, clock = HandlerRegistry(), FakeClock()
    reg.register("ok")(lambda p: _noop())
    q = MemoryJobQueue(reg, clock)
    await q.enqueue("ok", {}, run_at=T0 + timedelta(minutes=5))
    assert await q.run_pending() == 0
    clock.now = T0 + timedelta(minutes=5)
    assert await q.run_pending() == 1


async def _noop() -> None:
    return None


def test_backoff_formula() -> None:
    assert [backoff_seconds(n) for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert backoff_seconds(50) == 3600


async def test_job_retry_backoff_then_failed() -> None:
    reg, clock, calls = HandlerRegistry(), FakeClock(), []

    @reg.register("boom")
    async def boom(payload: dict[str, Any]) -> None:
        calls.append(clock.now)
        raise RuntimeError("nope")

    q = MemoryJobQueue(reg, clock)
    jid = await q.enqueue("boom", {}, max_attempts=3)
    job = q.jobs[jid]
    assert await q.run_pending() == 1 and job.status == "pending"
    assert job.run_at == T0 + timedelta(seconds=30)
    clock.now = T0 + timedelta(seconds=29)
    assert await q.run_pending() == 0
    clock.now = T0 + timedelta(seconds=30)
    assert await q.run_pending() == 1
    assert job.run_at == T0 + timedelta(seconds=30 + 60)
    clock.now = job.run_at
    assert await q.run_pending() == 1
    assert job.status == "failed" and job.attempts == 3
    assert job.last_error == "RuntimeError: nope"
    clock.now += timedelta(days=1)
    assert await q.run_pending() == 0 and len(calls) == 3


async def test_unknown_handler_ends_failed() -> None:
    q = MemoryJobQueue(HandlerRegistry(), FakeClock())
    jid = await q.enqueue("missing", {}, max_attempts=1)
    await q.run_pending()
    assert q.jobs[jid].status == "failed"


def test_duplicate_handler_rejected() -> None:
    reg = HandlerRegistry()
    reg.register("a")(lambda p: _noop())
    with pytest.raises(ValueError):
        reg.register("a")(lambda p: _noop())


async def test_realtime_poll_cursor() -> None:
    rt = MemoryRealtime()
    assert await rt.poll("c", 0) == ([], 0)
    await rt.publish("c", {"n": 1})
    await rt.publish("c", {"n": 2})
    await rt.publish("other", {"n": 9})
    events, cur = await rt.poll("c", 0)
    assert events == [{"n": 1}, {"n": 2}] and cur == 2
    assert await rt.poll("c", cur) == ([], 2)
    await rt.publish("c", {"n": 3})
    assert await rt.poll("c", cur) == ([{"n": 3}], 3)


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_provider_selection_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "memory")
    monkeypatch.setenv("MAILER_BACKEND", "memory")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.storage_backend, s.mailer_backend) == ("memory", "memory")
    validate_backends(s)
    assert isinstance(build_storage(s), MemoryStorage)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path))
    assert isinstance(build_storage(Settings(_env_file=None)), LocalFileStorage)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "field", ["storage_backend", "mailer_backend", "jobqueue_backend", "realtime_backend"]
)
def test_unknown_backend_fails_clearly(field: str) -> None:
    with pytest.raises(UnknownBackendError, match="not supported"):
        validate_backends(_settings(**{field: "s3"}))
