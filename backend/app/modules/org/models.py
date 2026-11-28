"""Model organisasi (CORE-ORG). `Tenant` = tabel GLOBAL (tanpa BaseRecord/RLS)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import uuid7
from app.framework.record import BaseRecord, tenant_fk, tenant_fk_column

NOT_DELETED = text("deleted_at IS NULL")


class Tenant(Base):
    """Tabel global. Role runtime hanya SELECT (lihat migrasi); tulis lewat role owner/platform."""

    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Company(BaseRecord, Base):
    __tablename__ = "companies"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        Index(
            "uq_companies_tenant_id_code",
            "tenant_id",
            "code",
            unique=True,
            postgresql_where=NOT_DELETED,
        ),
        CheckConstraint("fiscal_year_start_month BETWEEN 1 AND 12", name="fiscal_month"),
        CheckConstraint("default_language IN ('id', 'en')", name="language"),
        CheckConstraint("functional_currency ~ '^[A-Z]{3}$'", name="currency"),
    )

    code: Mapped[str] = mapped_column(String(32))
    legal_name: Mapped[str] = mapped_column(String(200))
    npwp: Mapped[str | None] = mapped_column(String(16))  # digit saja (15/16)
    address: Mapped[str] = mapped_column(Text, server_default=text("''"))
    logo_key: Mapped[str | None] = mapped_column(String(512))  # key port ObjectStorage
    functional_currency: Mapped[str] = mapped_column(String(3))
    timezone: Mapped[str] = mapped_column(String(64))
    default_language: Mapped[str] = mapped_column(String(2))
    fiscal_year_start_month: Mapped[int] = mapped_column(SmallInteger)
    # CORE-ORG-05; ubah dengan meng-assign dict baru (bukan mutasi in-place).
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class Branch(BaseRecord, Base):
    __tablename__ = "branches"
    __table_args__ = (
        tenant_fk("companies", "company_id", ondelete="RESTRICT"),
        # Target FK komposit warehouses → branches (menjamin gudang & cabang satu company).
        UniqueConstraint("tenant_id", "company_id", "id"),
        Index(
            "uq_branches_tenant_id_company_id_code",
            "tenant_id",
            "company_id",
            "code",
            unique=True,
            postgresql_where=NOT_DELETED,
        ),
    )

    company_id: Mapped[UUID] = tenant_fk_column()
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(Text, server_default=text("''"))
    timezone: Mapped[str | None] = mapped_column(String(64))  # NULL → ikut company


class Warehouse(BaseRecord, Base):
    __tablename__ = "warehouses"
    __table_args__ = (
        tenant_fk("companies", "company_id", ondelete="RESTRICT"),
        # FK 3 kolom: branch wajib satu company; branch_id NULL melewati FK (MATCH SIMPLE).
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "branch_id"],
            ["branches.tenant_id", "branches.company_id", "branches.id"],
            name="fk_warehouses_branch_same_company",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_warehouses_tenant_id_company_id_code",
            "tenant_id",
            "company_id",
            "code",
            unique=True,
            postgresql_where=NOT_DELETED,
        ),
        Index(
            "ix_warehouses_tenant_id_company_id_branch_id", "tenant_id", "company_id", "branch_id"
        ),
    )

    company_id: Mapped[UUID] = tenant_fk_column()
    branch_id: Mapped[UUID | None] = tenant_fk_column(nullable=True)
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
