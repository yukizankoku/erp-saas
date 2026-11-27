"""Pemilihan adapter dari settings (env). Dependency FastAPI: `Depends(get_storage)` dst."""

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


def validate_backends(s: Settings) -> None:
    """Panggil saat startup agar konfigurasi salah gagal cepat."""
    build_storage(s)
    build_mailer(s)
    build_job_queue(s)
    build_realtime(s)


# Singleton per proses (adapter in-memory harus berbagi state antar request).
@lru_cache
def get_storage() -> ObjectStorage:
    return build_storage(get_settings())


@lru_cache
def get_mailer() -> Mailer:
    return build_mailer(get_settings())


@lru_cache
def _job_backend() -> MemoryJobQueue:
    return build_job_queue(get_settings())


def get_job_queue() -> JobQueue:
    return _job_backend()


def get_job_runner() -> JobRunner:
    return _job_backend()


@lru_cache
def get_realtime() -> Realtime:
    return build_realtime(get_settings())


def get_pdf_renderer() -> PdfRenderer:
    return UnavailablePdfRenderer()
