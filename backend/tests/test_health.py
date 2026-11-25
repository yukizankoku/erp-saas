import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import engine_options, get_engine, to_async_url
from app.main import create_app


async def _get(path: str) -> tuple[int, dict]:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path)
    return response.status_code, response.json()


async def test_health_returns_ok() -> None:
    code, body = await _get("/api/v1/health")
    assert code == 200
    assert body["status"] == "ok"
    assert body["app"] == get_settings().app_name


def test_app_name_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv("APP_NAME", "Nama Uji")
    get_settings.cache_clear()
    try:
        assert get_settings().app_name == "Nama Uji"
    finally:
        get_settings.cache_clear()


def test_unpooled_alias_from_neon_env_pull(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgresql://u:p@ep-x.neon.tech/db")
    get_settings.cache_clear()
    try:
        assert get_settings().database_url_direct == "postgresql://u:p@ep-x.neon.tech/db"
    finally:
        get_settings.cache_clear()


def test_app_database_url_takes_precedence(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://neondb_owner:x@ep-a-pooler.neon.tech/neondb")
    monkeypatch.setenv(
        "APP_DATABASE_URL", "postgresql://app_runtime:y@ep-a-pooler.neon.tech/neondb"
    )
    get_settings.cache_clear()
    try:
        assert (
            get_settings().database_url == "postgresql://app_runtime:y@ep-a-pooler.neon.tech/neondb"
        )
    finally:
        get_settings.cache_clear()


def test_to_async_url() -> None:
    assert to_async_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert to_async_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert to_async_url("postgresql+asyncpg://x") == "postgresql+asyncpg://x"


def test_to_async_url_neon_params() -> None:
    url = "postgresql://u:p@ep-a-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    assert to_async_url(url) == (
        "postgresql+asyncpg://u:p@ep-a-pooler.ap-southeast-1.aws.neon.tech/neondb?ssl=require"
    )


def test_engine_options_serverless_uses_null_pool() -> None:
    assert engine_options(0)["poolclass"] is NullPool
    assert engine_options(5)["pool_size"] == 5
    assert engine_options(5)["connect_args"]["statement_cache_size"] == 0


async def test_health_db_not_configured(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    try:
        code, body = await _get("/api/v1/health/db")
    finally:
        get_settings.cache_clear()
    assert code == 503
    assert body["status"] == "not_configured"


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="butuh DATABASE_URL (CI / dev_pg.sh)"
)
async def test_health_db_against_real_database() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()
    try:
        code, body = await _get("/api/v1/health/db")
    finally:
        await get_engine().dispose()
        get_engine.cache_clear()
    assert code == 200, body
    assert body["status"] == "ok"
    assert body["latency_ms"] is not None
