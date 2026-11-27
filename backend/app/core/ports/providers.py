"""Pemilihan adapter dari settings (env). Dependency FastAPI: `Depends(get_storage)` dst."""

import logging
from collections.abc import Callable
from functools import lru_cache

from app.core.adapters.jobs import MemoryJobQueue
from app.core.adapters.mailer import ConsoleMailer, MemoryMailer
from app.core.adapters.pdf import UnavailablePdfRenderer
from app.core.adapters.realtime import MemoryRealtime
from app.core.adapters.storage import LocalFileStorage, MemoryStorage, UrlSigner
from app.core.config import Settings, get_settings
from app.core.ports.jobs import JobQueue, JobRunner
from app.core.ports.mailer import Mailer
from app.core.ports.pdf import PdfRenderer
from app.core.ports.realtime import Realtime
from app.core.ports.storage import ObjectStorage

logger = logging.getLogger(__name__)


class UnknownBackendError(RuntimeError):
    pass


def _choose[T](setting: str, value: str, options: dict[str, Callable[[], T]]) -> T:
    try:
        factory = options[value]
    except KeyError:
        allowed = ", ".join(sorted(options))
        raise UnknownBackendError(
            f"{setting}={value!r} is not supported (allowed: {allowed})"
        ) from None
    return factory()


def build_storage(s: Settings) -> ObjectStorage:
    signer = UrlSigner(s.storage_signing_secret)
    options: dict[str, Callable[[], ObjectStorage]] = {
        "local": lambda: LocalFileStorage(s.storage_local_root, signer),
        "memory": lambda: MemoryStorage(signer),
    }
    return _choose("STORAGE_BACKEND", s.storage_backend, options)


def build_mailer(s: Settings) -> Mailer:
    options: dict[str, Callable[[], Mailer]] = {"console": ConsoleMailer, "memory": MemoryMailer}
    return _choose("MAILER_BACKEND", s.mailer_backend, options)


def build_job_queue(s: Settings) -> MemoryJobQueue:
    return _choose("JOBQUEUE_BACKEND", s.jobqueue_backend, {"memory": MemoryJobQueue})


def build_realtime(s: Settings) -> Realtime:
    return _choose("REALTIME_BACKEND", s.realtime_backend, {"memory": MemoryRealtime})


class UnsafeBackendError(RuntimeError):
    """Backend dev/test dipakai di staging/production."""


_GUARDED_ENVS = ("staging", "production")


def unsafe_reasons(s: Settings) -> dict[str, str]:
    """Komponen -> alasan tak aman di staging/production. Tanpa nilai secret. Tidak melempar."""
    if s.environment not in _GUARDED_ENVS:
        return {}
    dev = {
        "storage": (s.storage_backend, {"local", "memory"}),
        "mailer": (s.mailer_backend, {"console", "memory"}),
        "jobqueue": (s.jobqueue_backend, {"memory"}),
        "realtime": (s.realtime_backend, {"memory"}),
    }
    out = {
        name: f"{name} backend {value!r} is for dev/test only"
        for name, (value, banned) in dev.items()
        if value in banned
    }
    if len(s.storage_signing_secret or "") < 32:
        reason = "STORAGE_SIGNING_SECRET is missing or shorter than 32 characters"
        out["storage"] = f"{out['storage']}; {reason}" if "storage" in out else reason
    return out


def warn_unsafe_backends(s: Settings) -> None:
    """Dipanggil `create_app()`: hanya log peringatan (aplikasi tetap boot)."""
    for name, reason in unsafe_reasons(s).items():
        logger.warning("unsafe %s in %s: %s", name, s.environment, reason)


def _guard(s: Settings, component: str) -> None:
    reason = unsafe_reasons(s).get(component)
    if reason:
        raise UnsafeBackendError(f"{reason} (environment={s.environment})")


# Singleton per proses (adapter in-memory harus berbagi state antar request).
@lru_cache
def get_storage() -> ObjectStorage:
    settings = get_settings()
    _guard(settings, "storage")
    return build_storage(settings)


@lru_cache
def get_mailer() -> Mailer:
    settings = get_settings()
    _guard(settings, "mailer")
    return build_mailer(settings)


@lru_cache
def _job_backend() -> MemoryJobQueue:
    return build_job_queue(get_settings())


def get_job_queue() -> JobQueue:
    _guard(get_settings(), "jobqueue")
    return _job_backend()


def get_job_runner() -> JobRunner:
    _guard(get_settings(), "jobqueue")
    return _job_backend()


@lru_cache
def get_realtime() -> Realtime:
    settings = get_settings()
    _guard(settings, "realtime")
    return build_realtime(settings)


def get_pdf_renderer() -> PdfRenderer:
    return UnavailablePdfRenderer()
