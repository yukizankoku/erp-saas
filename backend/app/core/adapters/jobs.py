"""Job queue in-memory (dev/test). Versi tabel Postgres = ADR 0005 bagian B."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.core.ports.jobs import (
    FAILED,
    PENDING,
    SUCCEEDED,
    HandlerRegistry,
    JobQueue,
    JobRunner,
    backoff_seconds,
    job_handlers,
)


@dataclass(slots=True)
class Job:
    id: str
    name: str
    payload: dict[str, Any]
    run_at: datetime
    max_attempts: int
    status: str = PENDING
    attempts: int = 0
    last_error: str | None = None
    idempotency_key: str | None = None


class MemoryJobQueue:
    def __init__(
        self,
        registry: HandlerRegistry | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._registry = registry or job_handlers
        self._clock = clock
        self.jobs: dict[str, Job] = {}

    async def enqueue(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        run_at: datetime | None = None,
        max_attempts: int = 5,
        idempotency_key: str | None = None,
    ) -> str:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if idempotency_key is not None:
            for existing in self.jobs.values():
                if existing.name == name and existing.idempotency_key == idempotency_key:
                    return existing.id
        job = Job(str(uuid4()), name, dict(payload), run_at or self._clock(), max_attempts)
        job.idempotency_key = idempotency_key
        self.jobs[job.id] = job
        return job.id

    async def run_pending(self) -> int:
        now = self._clock()
        due = [j for j in self.jobs.values() if j.status == PENDING and j.run_at <= now]
        for job in due:
            job.attempts += 1
            try:
                await self._registry.get(job.name)(dict(job.payload))
            except Exception as exc:  # dicatat di job, bukan ditelan: retry atau status failed
                job.last_error = f"{type(exc).__name__}: {exc}"
                if job.attempts >= job.max_attempts:
                    job.status = FAILED
                else:
                    job.run_at = now + timedelta(seconds=backoff_seconds(job.attempts))
            else:
                job.status = SUCCEEDED
                job.last_error = None
        return len(due)


_a: JobQueue = MemoryJobQueue()
_b: JobRunner = MemoryJobQueue()
