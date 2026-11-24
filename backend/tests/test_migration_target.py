"""Database tujuan migrasi Alembic: hanya pemanggil, `-x db_url`, atau env proses (bukan .env)."""

import io
import os
from argparse import Namespace
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.core.config import Settings, get_settings
from app.core.migration_target import (
    MIGRATION_ENV_VARS,
    describe_target,
    select_migration_target,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
LOCAL = "postgresql://erp:local-secret@localhost:5434/erp"
# Host & password fiktif berbentuk Neon — test tidak pernah membuka koneksi (mode offline).
NEON_POOLED = "postgresql://owner:neon-secret@ep-fake-000000-pooler.example.neon.tech/neondb"
NEON_DIRECT = (
    "postgresql://owner:neon-secret@ep-fake-000000.example.neon.tech/neondb?sslmode=require"
)


@pytest.fixture
def neon_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """`.env.local` ala `neon env pull` dibaca `Settings`; env proses hanya DATABASE_URL lokal."""
    env_file = tmp_path / ".env.local"
    env_file.write_text(f"DATABASE_URL={NEON_POOLED}\nDATABASE_URL_UNPOOLED={NEON_DIRECT}\n")
    monkeypatch.setitem(Settings.model_config, "env_file", (env_file,))
    for name in MIGRATION_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", LOCAL)
    get_settings.cache_clear()
    yield env_file
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("environ", "x_args", "expected"),
    [
        ({"DATABASE_URL": "u1"}, {}, ("u1", "DATABASE_URL")),
        ({"DATABASE_URL": "u1", "DATABASE_URL_DIRECT": "u2"}, {}, ("u2", "DATABASE_URL_DIRECT")),
        (
            {"DATABASE_URL": "u1", "DATABASE_URL_DIRECT": "u2", "DATABASE_URL_UNPOOLED": "u3"},
            {},
            ("u3", "DATABASE_URL_UNPOOLED"),
        ),
        ({"DATABASE_URL_UNPOOLED": "u3"}, {"db_url": "x"}, ("x", "-x db_url")),
        # Nilai kosong / spasi dianggap tidak di-set.
        (
            {"DATABASE_URL_UNPOOLED": " ", "DATABASE_URL": "u1"},
            {"db_url": ""},
            ("u1", "DATABASE_URL"),
        ),
    ],
)
def test_select_migration_target_precedence(
    environ: dict[str, str], x_args: dict[str, str], expected: tuple[str, str]
) -> None:
    target = select_migration_target(environ, x_args)
    assert (target.url, target.source) == expected


def test_select_migration_target_requires_explicit_url() -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL_UNPOOLED"):
        select_migration_target({"DATABASE_URL_POOLED": "x", "NEON_BRANCH": "y"}, {})


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (LOCAL, "host=localhost port=5434 database=erp user=erp"),
        (NEON_DIRECT, "host=ep-fake-000000.example.neon.tech port=5432 database=neondb user=owner"),
        (
            "postgresql:///erp?host=/run/postgresql&password=q-secret&user=erp",
            "host=/run/postgresql port=5432 database=erp user=erp",
        ),
        ("postgresql+asyncpg://h:notaport/db", "host=h port=? database=db user=?"),
    ],
)
def test_describe_target_never_includes_password(url: str, expected: str) -> None:
    described = describe_target(url)
    assert described == expected
    assert "secret" not in described


def test_settings_env_file_does_not_change_migration_target(neon_env_file: Path) -> None:
    # Bahaya yang ditutup: Settings mengambil DATABASE_URL_UNPOOLED Neon dari env file walau
    # proses hanya meng-export DATABASE_URL lokal.
    assert get_settings().database_url_direct == NEON_DIRECT
    target = select_migration_target(os.environ, {})
    assert (target.url, target.source) == (LOCAL, "DATABASE_URL")


def _run_offline_upgrade(x: list[str] | None = None) -> None:
    # Mode offline (--sql): env.py memilih URL & mencetak tujuan tanpa membuka koneksi.
    config = Config(output_buffer=io.StringIO())
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.cmd_opts = Namespace(x=x or [])
    command.upgrade(config, "head", sql=True)


def test_alembic_env_uses_process_env_not_env_file(
    neon_env_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run_offline_upgrade()
    err = capsys.readouterr().err
    assert "host=localhost port=5434 database=erp" in err
    assert "DATABASE_URL" in err
    assert "neon" not in err and "secret" not in err


def test_alembic_env_x_db_url_wins(
    neon_env_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgresql://erp@unpooled-host/erp")
    _run_offline_upgrade(["db_url=postgresql://erp:x-secret@x-host:6543/erp_x"])
    err = capsys.readouterr().err
    assert "host=x-host port=6543 database=erp_x" in err
    assert "-x db_url" in err
    assert "unpooled-host" not in err and "secret" not in err
