"""Fixture test integrasi pada PostgreSQL nyata dengan RLS aktif (ADR 0004 §8).

- URL hanya dari env `DATABASE_URL` (sengaja bukan `Settings`, yang juga membaca `.env.local`
  berisi database Neon). Tanpa `DATABASE_URL`: skip di lokal, **gagal** bila `CI=true`.
- `admin_engine`: role dari URL, wajib superuser (DDL, role, seed). Database non-superuser
  (mis. Neon) ditolak agar test tidak pernah membuat role/tabel di database bersama.
- `app_engine`: koneksi yang `SET ROLE app_rls_test` (NOLOGIN NOSUPERUSER NOBYPASSRLS) saat
  connect sehingga semua query aplikasi di test berjalan di bawah RLS; guard memastikan role aktif
  benar-benar tidak bisa melewati RLS (mencegah hijau palsu).
- Tabel uji dibuat ulang per sesi pytest lewat helper RLS produksi dan dihapus di akhir.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.db import engine_options, to_async_url
from app.core.tenancy.rls import tenant_rls_sql
from tests.integration.models import IntegrationBase, Item, ItemChild

APP_TEST_ROLE = "app_rls_test"

ROLE_SQL = [
    f"""
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_TEST_ROLE}') THEN
        CREATE ROLE {APP_TEST_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;
      END IF;
    END
    $$
    """,
    # Paksa atribut aman walau role sudah ada dari run sebelumnya.
    f"ALTER ROLE {APP_TEST_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE",
    f"GRANT USAGE ON SCHEMA public TO {APP_TEST_ROLE}",
]


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return to_async_url(url)
    if os.environ.get("CI", "").lower() == "true":
        pytest.fail(
            "DATABASE_URL wajib di CI: test integrasi RLS tidak boleh di-skip", pytrace=False
        )
    pytest.skip("butuh DATABASE_URL (CI / scripts/dev_pg.sh)")


async def _is_superuser(url: str) -> bool:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return bool(
                await conn.scalar(
                    text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
                )
            )
    finally:
        await engine.dispose()


async def _setup(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for statement in ROLE_SQL:
                await conn.execute(text(statement))
            await conn.run_sync(IntegrationBase.metadata.drop_all)
            await conn.run_sync(IntegrationBase.metadata.create_all)
            for model in (Item, ItemChild):
                for statement in tenant_rls_sql(model.__tablename__):
                    await conn.execute(text(statement))
                await conn.execute(
                    text(
                        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {model.__tablename__} "
                        f"TO {APP_TEST_ROLE}"
                    )
                )
    finally:
        await engine.dispose()


async def _teardown(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(IntegrationBase.metadata.drop_all)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _database_url()
    # Semua fixture (termasuk meta-test yang menjalankan migrasi) melewati guard ini.
    if not asyncio.run(_is_superuser(url)):
        pytest.fail(
            "DATABASE_URL harus menunjuk ke database uji lokal/CI dengan role superuser",
            pytrace=False,
        )
    return url


@pytest.fixture(scope="session")
def rls_tables(database_url: str) -> Iterator[None]:
    asyncio.run(_setup(database_url))
    yield
    asyncio.run(_teardown(database_url))


@pytest.fixture
async def admin_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app_engine(database_url: str, rls_tables: None) -> AsyncIterator[AsyncEngine]:
    # Opsi engine produksi (NullPool, tanpa cache prepared statement) + SET ROLE ke role uji.
    engine = create_async_engine(database_url, **engine_options(0))

    @event.listens_for(engine.sync_engine, "connect")
    def _set_role(dbapi_connection: Any, connection_record: Any) -> None:
        # Di luar transaksi adapter → bertahan selama koneksi hidup (NullPool: satu checkout).
        dbapi_connection.run_async(lambda conn: conn.execute(f"SET ROLE {APP_TEST_ROLE}"))

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            )
        ).one()
    if tuple(row) != (APP_TEST_ROLE, False, False):
        pytest.fail(f"role aplikasi test dapat melewati RLS: {tuple(row)}", pytrace=False)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)
