"""Helper Row-Level Security per tabel bisnis (ADR 0004 §5).

`tenant_rls_sql` / `drop_tenant_rls_sql` menghasilkan SQL murni (dipakai juga fixture test);
`enable_tenant_rls` / `disable_tenant_rls` membungkusnya untuk migrasi Alembic:

    def upgrade() -> None:
        op.create_table("sales_orders", ...)
        enable_tenant_rls("sales_orders")

    def downgrade() -> None:
        disable_tenant_rls("sales_orders")
        op.drop_table("sales_orders")

`NULLIF` wajib: setelah pernah di-set dalam sesi, `current_setting(..., true)` bernilai `''`
(bukan NULL). Tanpa konteks → `tenant_id = NULL` → 0 baris, INSERT/UPDATE melanggar WITH CHECK.
`current_setting` STABLE sehingga indeks berawalan `tenant_id` tetap dipakai.
Superuser & role BYPASSRLS selalu melewati RLS (termasuk FORCE) — lihat `docs/runbooks/db-roles.md`.
"""

import re

TENANT_SETTING = "app.tenant_id"
POLICY_NAME = "tenant_isolation"
TENANT_PREDICATE = f"tenant_id = NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"

_TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_IDENTIFIER_LENGTH = 63  # NAMEDATALEN - 1 di PostgreSQL


def validate_table_name(table: str) -> str:
    """Tolak nama tabel di luar `^[a-z][a-z0-9_]*$` (≤ 63 karakter) — anti-injeksi SQL."""
    if (
        not isinstance(table, str)
        or len(table) > _MAX_IDENTIFIER_LENGTH
        or _TABLE_NAME.fullmatch(table) is None
    ):
        raise ValueError(f"invalid table name for RLS: {table!r}")
    return table


def tenant_rls_sql(table: str) -> list[str]:
    """SQL untuk mengaktifkan RLS + FORCE + policy isolasi tenant pada `table`."""
    t = validate_table_name(table)
    return [
        f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {POLICY_NAME} ON {t} "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})",
    ]


def drop_tenant_rls_sql(table: str) -> list[str]:
    """Kebalikan `tenant_rls_sql` (untuk downgrade)."""
    t = validate_table_name(table)
    return [
        f"DROP POLICY IF EXISTS {POLICY_NAME} ON {t}",
        f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY",
    ]


def enable_tenant_rls(table: str) -> None:
    """Pembungkus Alembic: panggil di `upgrade()` setelah `op.create_table`."""
    from alembic import op

    for statement in tenant_rls_sql(table):
        op.execute(statement)


def disable_tenant_rls(table: str) -> None:
    """Pembungkus Alembic: panggil di `downgrade()` sebelum `op.drop_table`."""
    from alembic import op

    for statement in drop_tenant_rls_sql(table):
        op.execute(statement)
