from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.db import to_async_url
from app.main import create_app


async def test_health_returns_ok() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == get_settings().app_name


def test_app_name_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv("APP_NAME", "Nama Uji")
    get_settings.cache_clear()
    try:
        assert get_settings().app_name == "Nama Uji"
    finally:
        get_settings.cache_clear()


def test_to_async_url() -> None:
    assert to_async_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert to_async_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert to_async_url("postgresql+asyncpg://x") == "postgresql+asyncpg://x"
