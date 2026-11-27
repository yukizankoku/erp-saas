"""Adapter storage dev/test: filesystem lokal & in-memory, dengan URL bertanda tangan HMAC."""

import hashlib
import hmac
import json
import secrets
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode

from app.core.ports.storage import (
    InvalidStorageKey,
    ObjectStorage,
    PresignedUrl,
    assert_tenant_owned,
)
from app.core.tenancy.context import TenantContextMissing

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UrlSigner:
    """Tanda tangan HMAC-SHA256 atas (method, key, exp, content_type, max_bytes)."""

    def __init__(self, secret: str | None = None, clock: Clock = _utcnow) -> None:
        # Tanpa secret: acak per proses; tidak pernah ada secret bawaan di kode.
        self._secret = (secret or secrets.token_urlsafe(32)).encode()
        self._clock = clock

    def _sig(self, method: str, key: str, exp: int, content_type: str, max_bytes: int) -> str:
        # JSON = pengkodean tak ambigu (tanpa pemisah yang bisa disuntik lewat field).
        msg = json.dumps(
            [method, key, exp, content_type, max_bytes], separators=(",", ":")
        ).encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def sign(
        self, method: str, key: str, expires_in: int, content_type: str = "", max_bytes: int = 0
    ) -> PresignedUrl:
        assert_tenant_owned(key)
        if expires_in <= 0:
            raise ValueError("expires_in must be positive")
        if any(unicodedata.category(c)[0] == "C" for c in content_type):
            raise ValueError("invalid content_type")
        if method == "PUT" and max_bytes <= 0:
            raise ValueError("max_bytes must be positive for uploads")
        expires_at = self._clock() + timedelta(seconds=expires_in)
        exp = int(expires_at.timestamp())
        params = {"exp": exp, "sig": self._sig(method, key, exp, content_type, max_bytes)}
        headers: dict[str, str] = {}
        if method == "PUT":
            params["max_bytes"] = max_bytes
            headers["Content-Type"] = content_type
        return PresignedUrl(
            url=f"/_local-storage/{quote(key, safe='/')}?{urlencode(params)}",
            method=method,
            expires_at=expires_at,
            headers=headers,
        )

    def verify_signed(
        self,
        method: str,
        key: str,
        exp: int,
        sig: str,
        content_type: str = "",
        max_bytes: int = 0,
    ) -> bool:
        try:
            assert_tenant_owned(key)
        except (InvalidStorageKey, TenantContextMissing):
            return False
        if self._clock().timestamp() > exp:
            return False
        expected = self._sig(method, key, exp, content_type, max_bytes)
        return hmac.compare_digest(expected, sig)


class _SignedStorage:
    signer: UrlSigner

    async def presigned_upload_url(
        self, key: str, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedUrl:
        return self.signer.sign("PUT", key, expires_in, content_type, max_bytes)

    async def presigned_download_url(self, key: str, expires_in: int) -> PresignedUrl:
        return self.signer.sign("GET", key, expires_in)


class MemoryStorage(_SignedStorage):
    def __init__(self, signer: UrlSigner | None = None) -> None:
        self.signer = signer or UrlSigner()
        self._objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self._objects[assert_tenant_owned(key)] = (data, content_type)

    async def get(self, key: str) -> bytes:
        try:
            return self._objects[assert_tenant_owned(key)][0]
        except KeyError:
            raise FileNotFoundError(key) from None

    async def delete(self, key: str) -> None:
        self._objects.pop(assert_tenant_owned(key), None)

    async def exists(self, key: str) -> bool:
        return assert_tenant_owned(key) in self._objects


class LocalFileStorage(_SignedStorage):
    def __init__(self, root: str | Path, signer: UrlSigner | None = None) -> None:
        self.signer = signer or UrlSigner()
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self._root / assert_tenant_owned(key)).resolve()
        if not path.is_relative_to(self._root):  # sabuk pengaman tambahan (mis. symlink)
            raise InvalidStorageKey("key escapes storage root")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def exists(self, key: str) -> bool:
        return self._path(key).is_file()


_a: ObjectStorage = MemoryStorage()
_b: ObjectStorage = LocalFileStorage(".")
