"""org_structure: tenants (global), companies, branches, warehouses (CORE-ORG-01..05)

Grants (role aplikasi `app_runtime`, dilewati bila role belum ada: lokal/CI):
- tenants (global, tanpa RLS): SELECT saja. REVOKE ALL dulu agar `ALTER DEFAULT PRIVILEGES`
  (DML otomatis) tidak membuat runtime bisa UPDATE/DELETE tenant. Tulis = role owner/platform.
- companies/branches/warehouses: SELECT, INSERT, UPDATE, DELETE (dibatasi RLS FORCE).

Revision ID: 0001
Revises:
Create Date: 2026-11-28 03:10:53.723029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.tenancy.rls import disable_tenant_rls, enable_tenant_rls

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


GRANTS_SQL = """
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_runtime') THEN
    REVOKE ALL ON tenants FROM app_runtime;
    GRANT SELECT ON tenants TO app_runtime;
    GRANT SELECT, INSERT, UPDATE, DELETE ON companies, branches, warehouses TO app_runtime;
  END IF;
END
$$
"""


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'active'"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenants_slug")),
    )
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("npwp", sa.String(length=16), nullable=True),
        sa.Column("address", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("logo_key", sa.String(length=512), nullable=True),
        sa.Column("functional_currency", sa.String(length=3), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("default_language", sa.String(length=2), nullable=False),
        sa.Column("fiscal_year_start_month", sa.SmallInteger(), nullable=False),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("delete_batch_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "custom_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("default_language IN ('id', 'en')", name=op.f("ck_companies_language")),
        sa.CheckConstraint(
            "functional_currency ~ '^[A-Z]{3}$'", name=op.f("ck_companies_currency")
        ),
        sa.CheckConstraint(
            "fiscal_year_start_month BETWEEN 1 AND 12", name=op.f("ck_companies_fiscal_month")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_companies_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_companies_tenant_id_id"),
    )
    op.create_index(
        "uq_companies_tenant_id_code",
        "companies",
        ["tenant_id", "code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    enable_tenant_rls("companies")
    op.create_table(
        "branches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("address", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("delete_batch_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "custom_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name=op.f("fk_branches_tenant_id_companies"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_branches")),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name=op.f("uq_branches_tenant_id_company_id_id")
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_branches_tenant_id_id"),
    )
    op.create_index(
        "uq_branches_tenant_id_company_id_code",
        "branches",
        ["tenant_id", "company_id", "code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    enable_tenant_rls("branches")
    op.create_table(
        "warehouses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("branch_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("delete_batch_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "custom_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "branch_id"],
            ["branches.tenant_id", "branches.company_id", "branches.id"],
            name="fk_warehouses_branch_same_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name=op.f("fk_warehouses_tenant_id_companies"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_warehouses")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_warehouses_tenant_id_id"),
    )
    op.create_index(
        "ix_warehouses_tenant_id_company_id_branch_id",
        "warehouses",
        ["tenant_id", "company_id", "branch_id"],
        unique=False,
    )
    op.create_index(
        "uq_warehouses_tenant_id_company_id_code",
        "warehouses",
        ["tenant_id", "company_id", "code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    enable_tenant_rls("warehouses")
    op.execute(GRANTS_SQL)


def downgrade() -> None:
    op.drop_index(
        "uq_warehouses_tenant_id_company_id_code",
        table_name="warehouses",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_warehouses_tenant_id_company_id_branch_id", table_name="warehouses")
    disable_tenant_rls("warehouses")
    op.drop_table("warehouses")
    op.drop_index(
        "uq_branches_tenant_id_company_id_code",
        table_name="branches",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    disable_tenant_rls("branches")
    op.drop_table("branches")
    op.drop_index(
        "uq_companies_tenant_id_code",
        table_name="companies",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    disable_tenant_rls("companies")
    op.drop_table("companies")
    op.drop_table("tenants")
