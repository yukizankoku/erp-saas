"""Lingkungan migrasi Alembic (async).

Migrasi memakai koneksi *direct* (bukan pooled) — PRD §6.7. Database tujuan hanya dari koneksi
pemanggil, `-x db_url=...`, atau env proses DATABASE_URL_UNPOOLED → DATABASE_URL_DIRECT →
DATABASE_URL — **tidak** dari Settings/.env.local (`app/core/migration_target.py`). Tujuan dicetak
ke stderr (tanpa password) sebelum migrasi berjalan.
"""

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.db import Base, to_async_url
from app.core.migration_target import describe_target, select_migration_target

# Import model semua modul di sini agar terdeteksi autogenerate (diisi mulai Fase 1).
from app.modules.org import models as _org_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    target = select_migration_target(os.environ, context.get_x_argument(as_dictionary=True))
    print(
        f"alembic: database tujuan {describe_target(target.url)} (sumber: {target.source})",
        file=sys.stderr,
    )
    return to_async_url(target.url)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
elif (connection := config.attributes.get("connection")) is not None:
    # Dipanggil dari kode (mis. meta-test RLS) dengan koneksi milik pemanggil: URL tidak dibaca
    # sama sekali sehingga migrasi pasti berjalan di database pemanggil.
    _do_run_migrations(connection)
else:
    asyncio.run(run_migrations_online())
