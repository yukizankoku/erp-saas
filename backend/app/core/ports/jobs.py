"""Port `JobQueue`: antrean job dengan retry backoff eksponensial.

Backoff (tetap): setelah percobaan ke-n gagal (n = 1, 2, ...), job dijadwalkan ulang
`min(BACKOFF_BASE_SECONDS * 2**(n-1), BACKOFF_CAP_SECONDS)` detik kemudian
= 30, 60, 120, ... maks 3600.
Gagal pada percobaan ke-`max_attempts` -> status akhir `failed` (disimpan beserta `last_error`).
Status berupa string biasa (tanpa ENUM PostgreSQL).
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

PENDING = "pending"
SUCCEEDED = "succeeded"
FAILED = "failed"

BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 3600

JobHandler = Callable[[dict[str, Any]], Awaitable[None]]


def backoff_seconds(attempt: int) -> int:
    """Jeda sebelum percobaan berikutnya, setelah `attempt` (>= 1) kali gagal."""
    return int(min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_CAP_SECONDS))


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, name: str) -> Callable[[JobHandler], JobHandler]:
        def decorator(fn: JobHandler) -> JobHandler:
            if name in self._handlers:
                raise ValueError(f"job handler already registered: {name}")
            self._handlers[name] = fn
            return fn

        return decorator

    def get(self, name: str) -> JobHandler:
        return self._handlers[name]


# Registry global default; modul mendaftarkan handler dengan `@job_handlers.register("modul.nama")`.
job_handlers = HandlerRegistry()


@runtime_checkable
class JobQueue(Protocol):
    async def enqueue(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        run_at: datetime | None = None,
        max_attempts: int = 5,
        idempotency_key: str | None = None,
    ) -> str: ...


@runtime_checkable
class JobRunner(Protocol):
    async def run_pending(self) -> int:
        """Proses semua job yang jatuh tempo; kembalikan jumlah job yang dijalankan."""
        ...
