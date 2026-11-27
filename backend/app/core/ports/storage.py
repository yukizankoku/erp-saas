"""Port `ObjectStorage` + helper kunci objek berprefiks tenant (aman dari path traversal)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.core.tenancy.context import require_tenant_id


class InvalidStorageKey(ValueError):
    """Kunci objek tidak aman (traversal, path absolut, segmen kosong, dst.)."""


def validate_key(key: str) -> str:
    """Pastikan kunci = segmen relatif non-kosong dipisah `/`; kembalikan kunci apa adanya."""
    if not key or key.startswith("/") or "\\" in key or "\x00" in key:
        raise InvalidStorageKey("invalid storage key")
    for segment in key.split("/"):
        if segment in ("", ".", "..") or segment != segment.strip() or ":" in segment:
            raise InvalidStorageKey("invalid storage key segment")
    return key


def tenant_key(*segments: str, tenant_id: UUID | None = None) -> str:
    """Bangun `t/<tenant_id>/<seg>/...`; tenant diambil dari konteks bila tidak diberikan."""
    tid = tenant_id if tenant_id is not None else require_tenant_id()
    if not segments:
        raise InvalidStorageKey("at least one segment is required")
    return validate_key("/".join(("t", str(tid), *segments)))


@dataclass(frozen=True, slots=True)
class PresignedUrl:
    url: str
    method: str
    expires_at: datetime
    headers: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...

    async def presigned_upload_url(
        self, key: str, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedUrl: ...

    async def presigned_download_url(self, key: str, expires_in: int) -> PresignedUrl: ...
