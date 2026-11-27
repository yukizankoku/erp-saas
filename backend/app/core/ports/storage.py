"""Port `ObjectStorage` + helper kunci objek berprefiks tenant (aman dari path traversal)."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.core.tenancy.context import require_tenant_id


class InvalidStorageKey(ValueError):
    """Kunci objek tidak aman (traversal, path absolut, segmen kosong, dst.)."""


_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


def validate_key(key: str) -> str:
    """Kunci = segmen relatif dipisah `/`; tiap segmen hanya `[A-Za-z0-9._-]`, bukan `.`/`..`.

    Allowlist ini otomatis menolak kontrol/format char, `?#%\\:`, spasi, dan Unicode (tanpa
    ambiguitas normalisasi). Nama file dari pengguna harus disanitasi pemanggil.
    """
    if not isinstance(key, str) or not key:
        raise InvalidStorageKey("invalid storage key")
    for segment in key.split("/"):
        if segment in (".", "..") or _SEGMENT.fullmatch(segment) is None:
            raise InvalidStorageKey("invalid storage key segment")
    return key


def tenant_key(*segments: str) -> str:
    """Bangun `t/<tenant_id>/<seg>/...`; tenant SELALU dari konteks (tanpa override)."""
    tid = require_tenant_id()
    if not segments:
        raise InvalidStorageKey("at least one segment is required")
    return validate_key("/".join(("t", str(tid), *segments)))


def assert_tenant_owned(key: str) -> str:
    """Validasi kunci dan pastikan berawalan `t/<tenant konteks>/` (fail-closed tanpa konteks)."""
    validate_key(key)
    parts = key.split("/")
    if len(parts) < 3 or parts[0] != "t" or parts[1] != str(require_tenant_id()):
        raise InvalidStorageKey("key does not belong to the current tenant")
    return key


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
