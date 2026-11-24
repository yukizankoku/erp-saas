"""Koneksi database async (SQLAlchemy 2.x + asyncpg) dan metadata dasar.

Naming convention constraint wajib konsisten agar migrasi Alembic dapat dibalik (PRD §6.7).
Neon: koneksi pooled melewati PgBouncer mode transaksi, jadi cache prepared statement asyncpg
dimatikan dan state sesi hanya boleh di-set per transaksi (`set_config(..., true)`).
"""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Parameter libpq yang tidak dipahami asyncpg lewat URL SQLAlchemy.
_UNSUPPORTED_QUERY_PARAMS = {"channel_binding"}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def to_async_url(url: str) -> str:
    """Ubah URL PostgreSQL standar (mis. dari Neon) menjadi URL driver asyncpg.

    `sslmode=require` → `ssl=require`; `channel_binding` dibuang (tidak didukung asyncpg).
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix) :]
            break
    parts = urlsplit(url)
    if not parts.query:
        return url
    params = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key in _UNSUPPORTED_QUERY_PARAMS:
            continue
        params.append(("ssl" if key == "sslmode" else key, value))
    return urlunsplit(parts._replace(query=urlencode(params)))


def engine_options(pool_size: int) -> dict[str, Any]:
    """Opsi engine yang aman untuk PgBouncer (pooler Neon) maupun Postgres biasa."""
    options: dict[str, Any] = {
        "connect_args": {"statement_cache_size": 0},  # aman untuk PgBouncer mode transaksi
        "pool_pre_ping": True,
    }
    if pool_size <= 0:
        options["poolclass"] = NullPool
    else:
        options["pool_size"] = pool_size
    return options


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL belum diatur")
    return create_async_engine(
        to_async_url(settings.database_url),
        **engine_options(settings.database_pool_size),
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependency FastAPI: satu sesi per request."""
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        yield session
