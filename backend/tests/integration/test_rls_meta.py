"""Meta-test skema: setiap tabel ber-`tenant_id` wajib terlindungi RLS (ADR 0004 §5, §8).

Dijalankan pada skema hasil `alembic upgrade head` (dijalankan fixture di database uji, lewat
koneksi test — bukan URL dari settings). Selama belum ada tabel bisnis, daftar pelanggaran kosong;
test kontrol negatif/positif membuktikan pemeriksaannya benar-benar bekerja.
"""

from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.db import Base
from app.core.tenancy.rls import POLICY_NAME, TENANT_PREDICATE, TENANT_SETTING, tenant_rls_sql

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Bentuk deparse PostgreSQL (`pg_policies.qual` / `with_check`) dari `rls.TENANT_PREDICATE`.
# Dibandingkan persis: pola LIKE meloloskan policy longgar seperti `(<predikat>) OR true`.
EXPECTED_POLICY_EXPR = (
    "(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid)"
)

# Satu baris per tabel biasa/partisi yang punya kolom tenant_id di skema non-sistem.
AUDIT_SQL = text(
    r"""
    SELECT n.nspname AS schema,
           c.relname AS table,
           c.relrowsecurity AS rls_enabled,
           c.relforcerowsecurity AS rls_forced,
           EXISTS (
             SELECT 1 FROM pg_policies p
             WHERE p.schemaname = n.nspname
               AND p.tablename = c.relname
               AND p.policyname = :policy
               AND p.permissive = 'PERMISSIVE'
               AND p.cmd = 'ALL'
               AND p.roles = '{public}'::name[]
               AND p.qual = :expected
               AND p.with_check = :expected
           ) AS has_tenant_policy,
           ARRAY(
             SELECT p.polname::text FROM pg_policy p
             WHERE p.polrelid = c.oid AND p.polpermissive AND p.polname <> :policy
             ORDER BY 1
           ) AS other_permissive_policies,
           EXISTS (
             SELECT 1 FROM pg_index i
             WHERE i.indrelid = c.oid AND i.indkey[0] = a.attnum
           ) AS has_tenant_index
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a
      ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND a.attnum > 0 AND NOT a.attisdropped
    WHERE c.relkind IN ('r', 'p')
      AND n.nspname <> 'information_schema'
      AND n.nspname NOT LIKE 'pg\_%'
    ORDER BY 1, 2
    """
)


async def audit_tenant_tables(conn: AsyncConnection) -> tuple[list[str], list[str]]:
    """Kembalikan (tabel yang diperiksa, daftar pelanggaran)."""
    params = {"policy": POLICY_NAME, "expected": EXPECTED_POLICY_EXPR}
    rows = (await conn.execute(AUDIT_SQL, params)).mappings().all()
    checked: list[str] = []
    problems: list[str] = []
    for row in rows:
        name = f"{row['schema']}.{row['table']}"
        checked.append(name)
        if not row["rls_enabled"]:
            problems.append(f"{name}: RLS tidak aktif")
        if not row["rls_forced"]:
            problems.append(f"{name}: RLS tidak FORCE")
        if not row["has_tenant_policy"]:
            problems.append(f"{name}: policy {POLICY_NAME} tidak ada / tidak sesuai")
        if row["other_permissive_policies"]:
            problems.append(
                f"{name}: policy permissive lain melonggarkan isolasi "
                f"{row['other_permissive_policies']}"
            )
        if not row["has_tenant_index"]:
            problems.append(f"{name}: tidak ada indeks berawalan tenant_id")
    return checked, problems


async def test_audit_rejects_policy_that_only_resembles_tenant_predicate(
    admin_engine: AsyncEngine,
) -> None:
    """Policy longgar yang memuat predikat tenant (mis. `... OR true`) tidak boleh lolos."""
    loose = {
        # USING & WITH CHECK sama-sama dilonggarkan.
        "meta_probe_or_true": (f"({TENANT_PREDICATE}) OR true", f"({TENANT_PREDICATE}) OR true"),
        # Hanya WITH CHECK yang longgar → INSERT/UPDATE ke tenant lain.
        "meta_probe_check_or": (TENANT_PREDICATE, f"({TENANT_PREDICATE}) OR tenant_id IS NOT NULL"),
        # Nama setting lain yang diawali `app.tenant_id`.
        "meta_probe_other_guc": (
            TENANT_PREDICATE.replace(TENANT_SETTING, f"{TENANT_SETTING}_x"),
            TENANT_PREDICATE.replace(TENANT_SETTING, f"{TENANT_SETTING}_x"),
        ),
    }
    async with admin_engine.connect() as conn:
        for table, (using, check) in loose.items():
            await conn.execute(
                text(f"CREATE TABLE {table} (id uuid PRIMARY KEY, tenant_id uuid NOT NULL)")
            )
            await conn.execute(text(f"CREATE INDEX ix_{table} ON {table} (tenant_id)"))
            await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            await conn.execute(
                text(f"CREATE POLICY {POLICY_NAME} ON {table} USING ({using}) WITH CHECK ({check})")
            )
        checked, problems = await audit_tenant_tables(conn)
        await conn.rollback()

    for table in loose:
        assert f"public.{table}" in checked
        assert [p for p in problems if p.startswith(f"public.{table}:")] == [
            f"public.{table}: policy {POLICY_NAME} tidak ada / tidak sesuai"
        ]


def _alembic_config(connection: Connection) -> Config:
    # Tanpa file ini: env.py tidak memanggil fileConfig (logging pytest tetap utuh).
    config = Config()
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture
async def migrated_head(admin_engine: AsyncEngine) -> str | None:
    """Jalankan `alembic upgrade head` pada database uji; kembalikan revisi head."""

    def upgrade(connection: Connection) -> Any:
        config = _alembic_config(connection)
        command.upgrade(config, "head")
        return ScriptDirectory.from_config(config).get_current_head()

    async with admin_engine.begin() as conn:
        head: str | None = await conn.run_sync(upgrade)
    return head


async def test_every_tenant_table_after_migrations_is_protected(
    admin_engine: AsyncEngine, migrated_head: str | None
) -> None:
    async with admin_engine.connect() as conn:
        current = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        checked, problems = await audit_tenant_tables(conn)
    assert current == migrated_head  # benar-benar memeriksa skema hasil migrasi terbaru
    assert problems == []

    # Setiap model ber-tenant_id yang terdaftar harus ada di skema hasil migrasi (dan diperiksa).
    model_tables = {
        f"{t.schema or 'public'}.{t.name}"
        for t in Base.metadata.tables.values()
        if "tenant_id" in t.c
    }
    assert model_tables <= set(checked)


TENANT_FK_SQL = text(
    r"""
    SELECT c.relname AS tbl,
           EXISTS (
             SELECT 1 FROM pg_constraint u
             WHERE u.conrelid = c.oid AND u.contype IN ('u', 'p')
               AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
                    FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum = ANY (u.conkey))
                   = ARRAY['id', 'tenant_id']
           ) AS has_tenant_unique
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute t
      ON t.attrelid = c.oid AND t.attname = 'tenant_id' AND t.attnum > 0 AND NOT t.attisdropped
    WHERE c.relkind IN ('r', 'p') AND n.nspname = 'public'
    ORDER BY 1
    """
)

# FK menuju tabel ber-tenant_id yang tidak memuat tenant_id di kedua sisi.
NON_COMPOSITE_FK_SQL = text(
    r"""
    SELECT c.relname AS tbl, k.conname AS fk
    FROM pg_constraint k
    JOIN pg_class c ON c.oid = k.conrelid
    JOIN pg_class r ON r.oid = k.confrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE k.contype = 'f' AND n.nspname = 'public'
      AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = r.oid
                  AND a.attname = 'tenant_id' AND a.attnum > 0 AND NOT a.attisdropped)
      AND NOT (
        (SELECT a.attnum FROM pg_attribute a
         WHERE a.attrelid = r.oid AND a.attname = 'tenant_id') = ANY (k.confkey)
        AND (SELECT a.attnum FROM pg_attribute a
             WHERE a.attrelid = c.oid AND a.attname = 'tenant_id') = ANY (k.conkey)
        AND cardinality(k.conkey) >= 2
      )
    ORDER BY 1, 2
    """
)


async def audit_tenant_fks(conn: AsyncConnection) -> list[str]:
    problems = [
        f"{r['tbl']}: tidak ada UNIQUE (tenant_id, id)"
        for r in (await conn.execute(TENANT_FK_SQL)).mappings()
        if not r["has_tenant_unique"]
    ]
    problems += [
        f"{r['tbl']}: FK {r['fk']} bukan komposit tenant_id"
        for r in (await conn.execute(NON_COMPOSITE_FK_SQL)).mappings()
    ]
    return problems


async def test_tenant_tables_after_migrations_have_composite_fks(
    admin_engine: AsyncEngine, migrated_head: str | None
) -> None:
    """Berlaku pada skema hasil migrasi; kosong (vacuous) selama belum ada tabel bisnis."""
    async with admin_engine.connect() as conn:
        assert await audit_tenant_fks(conn) == []


async def test_fk_audit_flags_bad_tables(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as conn:
        await conn.execute(text("CREATE TABLE fkp_parent (id uuid PRIMARY KEY, tenant_id uuid)"))
        await conn.execute(
            text(
                "CREATE TABLE fkp_bad (id uuid PRIMARY KEY, tenant_id uuid, "
                "parent_id uuid REFERENCES fkp_parent (id))"
            )
        )
        problems = await audit_tenant_fks(conn)
        await conn.rollback()
    assert "fkp_parent: tidak ada UNIQUE (tenant_id, id)" in problems
    assert "fkp_bad: FK fkp_bad_parent_id_fkey bukan komposit tenant_id" in problems


async def test_audit_flags_unprotected_tenant_table(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as conn:
        await conn.execute(
            text("CREATE TABLE meta_probe_bad (id uuid PRIMARY KEY, tenant_id uuid)")
        )
        await conn.execute(text("CREATE TABLE meta_probe_global (id uuid PRIMARY KEY, name text)"))
        await conn.execute(
            text("CREATE TABLE meta_probe_good (id uuid PRIMARY KEY, tenant_id uuid NOT NULL)")
        )
        await conn.execute(
            text("CREATE INDEX ix_meta_probe_good ON meta_probe_good (tenant_id, id)")
        )
        for statement in tenant_rls_sql("meta_probe_good"):
            await conn.execute(text(statement))
        # RLS aktif tetapi tanpa FORCE, indeks tenant_id bukan kolom pertama, dan policy longgar.
        await conn.execute(
            text("CREATE TABLE meta_probe_weak (id uuid PRIMARY KEY, tenant_id uuid NOT NULL)")
        )
        await conn.execute(
            text("CREATE INDEX ix_meta_probe_weak ON meta_probe_weak (id, tenant_id)")
        )
        await conn.execute(text("ALTER TABLE meta_probe_weak ENABLE ROW LEVEL SECURITY"))
        await conn.execute(text("CREATE POLICY tenant_isolation ON meta_probe_weak USING (true)"))
        await conn.execute(text("CREATE POLICY allow_all ON meta_probe_weak USING (true)"))

        checked, problems = await audit_tenant_tables(conn)
        await conn.rollback()

    assert "public.meta_probe_global" not in checked
    assert "public.meta_probe_good" in checked
    assert not [p for p in problems if p.startswith("public.meta_probe_good:")]
    assert sorted(p for p in problems if p.startswith("public.meta_probe_bad:")) == sorted(
        [
            "public.meta_probe_bad: RLS tidak aktif",
            "public.meta_probe_bad: RLS tidak FORCE",
            f"public.meta_probe_bad: policy {POLICY_NAME} tidak ada / tidak sesuai",
            "public.meta_probe_bad: tidak ada indeks berawalan tenant_id",
        ]
    )
    assert sorted(p for p in problems if p.startswith("public.meta_probe_weak:")) == sorted(
        [
            "public.meta_probe_weak: RLS tidak FORCE",
            f"public.meta_probe_weak: policy {POLICY_NAME} tidak ada / tidak sesuai",
            "public.meta_probe_weak: policy permissive lain melonggarkan isolasi ['allow_all']",
            "public.meta_probe_weak: tidak ada indeks berawalan tenant_id",
        ]
    )
