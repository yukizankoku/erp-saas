"""Konfigurasi aplikasi dari environment variable (12-factor).

Nama & logo aplikasi sengaja dapat diganti tanpa mengubah kode (PRD: nama aplikasi belum final).
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "[APP_NAME]"
    app_short_name: str = "[APP_NAME]"
    app_logo_url: str | None = None
    environment: Literal["local", "test", "preview", "staging", "production"] = "local"

    # Aplikasi memakai koneksi pooled; migrasi memakai koneksi direct (Neon).
    database_url: str | None = None
    database_url_direct: str | None = None

    api_prefix: str = "/api/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
