"""jobs: antrean job berbasis tabel Postgres (ADR 0005 bagian B)

Tabel sistem ber-tenant (bukan BaseRecord): RLS FORCE. Grants `app_runtime` (dilewati bila role
belum ada): SELECT, INSERT, UPDATE; tanpa DELETE (riwayat job dipangkas oleh role owner).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-28 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.tenancy.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRANTS_SQL = """
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_runtime') THEN
    REVOKE ALL ON jobs FROM app_runtime;
    GRANT SELECT, INSERT, UPDATE ON jobs TO app_runtime;
  END IF;
END
$$
"""


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')", name=op.f("ck_jobs_status")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_jobs_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_jobs_tenant_id_id"),
    )
    op.create_index("ix_jobs_status_run_at", "jobs", ["status", "run_at"], unique=False)
    op.create_index(
        "ix_jobs_tenant_id_status_run_at", "jobs", ["tenant_id", "status", "run_at"], unique=False
    )
    op.create_index(
        "uq_jobs_tenant_id_name_idempotency_key",
        "jobs",
        ["tenant_id", "name", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    enable_tenant_rls("jobs")
    op.execute(GRANTS_SQL)


def downgrade() -> None:
    disable_tenant_rls("jobs")
    op.drop_table("jobs")
