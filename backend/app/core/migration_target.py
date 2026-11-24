"""Database tujuan migrasi Alembic (dipakai `migrations/env.py`).

URL migrasi **hanya** berasal dari (urutan prioritas):
1. koneksi milik pemanggil (`config.attributes["connection"]`, ditangani langsung di env.py);
2. `alembic -x db_url=...`;
3. env var **proses**: `DATABASE_URL_UNPOOLED` → `DATABASE_URL_DIRECT` → `DATABASE_URL`.

Sengaja tidak memakai `Settings`: Settings juga membaca `.env.local` (hasil `neon env pull`, berisi
koneksi Neon) sehingga migrasi bisa diam-diam menuju Neon walau proses meng-export `DATABASE_URL`
lokal. Migrasi ke Neon wajib eksplisit: `set -a; . ../.env.local; set +a` (lihat backend/README.md).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

MIGRATION_ENV_VARS = ("DATABASE_URL_UNPOOLED", "DATABASE_URL_DIRECT", "DATABASE_URL")
X_ARG_NAME = "db_url"
_DEFAULT_PORT = 5432


@dataclass(frozen=True, slots=True)
class MigrationTarget:
    url: str
    source: str  # mis. "-x db_url" atau nama environment variable

    def __repr__(self) -> str:  # jangan pernah menampilkan password lewat repr/log
        return f"MigrationTarget({describe_target(self.url)}, source={self.source!r})"


def select_migration_target(
    environ: Mapping[str, str], x_args: Mapping[str, str]
) -> MigrationTarget:
    """Pilih URL migrasi dari `-x db_url` lalu env proses; nilai kosong dianggap tidak di-set."""
    url = x_args.get(X_ARG_NAME, "").strip()
    if url:
        return MigrationTarget(url=url, source=f"-x {X_ARG_NAME}")
    for name in MIGRATION_ENV_VARS:
        value = environ.get(name, "").strip()
        if value:
            return MigrationTarget(url=value, source=name)
    raise RuntimeError(
        "URL migrasi tidak ditemukan: export salah satu dari "
        f"{', '.join(MIGRATION_ENV_VARS)} atau jalankan `alembic -x {X_ARG_NAME}=...` "
        "(.env/.env.local sengaja tidak dibaca)"
    )


def describe_target(url: str) -> str:
    """Ringkasan tujuan tanpa password: `host=… port=… database=… user=…`."""
    parts = urlsplit(url)
    query = parse_qs(parts.query)

    def from_query(key: str) -> str | None:
        values = query.get(key)
        return values[0] if values else None

    host = parts.hostname or from_query("host") or "?"
    try:
        port: int | str = parts.port or int(from_query("port") or _DEFAULT_PORT)
    except ValueError:
        port = "?"
    database = unquote(parts.path.lstrip("/")) or from_query("dbname") or "?"
    user = unquote(parts.username or "") or from_query("user") or "?"
    return f"host={host} port={port} database={database} user={user}"
