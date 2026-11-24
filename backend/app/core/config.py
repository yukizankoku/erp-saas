"""Konfigurasi aplikasi dari environment variable (12-factor).

Nama & logo aplikasi sengaja dapat diganti tanpa mengubah kode (PRD: nama aplikasi belum final).

Urutan sumber (belakangan menang): `<repo>/.env`, `<repo>/.env.local` (dari `neon env pull`),
`backend/.env`, lalu environment variable proses.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_DIR = _BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_REPO_DIR / ".env", _REPO_DIR / ".env.local", _BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "[APP_NAME]"
    app_short_name: str = "[APP_NAME]"
    app_logo_url: str | None = None
    environment: Literal["local", "test", "preview", "staging", "production"] = "local"

    # Neon: aplikasi memakai koneksi pooled (DATABASE_URL, host "-pooler");
    # migrasi memakai koneksi direct (DATABASE_URL_UNPOOLED dari `neon env pull`).
    database_url: str | None = None
    database_url_direct: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL_DIRECT", "DATABASE_URL_UNPOOLED"),
    )
    # 0 = tanpa pool di aplikasi (NullPool) — disarankan untuk serverless (Vercel) karena
    # pooling sudah dilakukan PgBouncer Neon. >0 = ukuran pool untuk server berumur panjang (VPS).
    database_pool_size: int = 5

    api_prefix: str = "/api/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
