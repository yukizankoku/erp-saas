"""`BaseRecord`: kolom standar setiap model bisnis (PRD §6.7, ADR 0004 §4).

Pemakaian (mixin, didahulukan dari `Base`):

    class Product(BaseRecord, Base):
        __tablename__ = "inv_products"
        __table_args__ = (
            Index("ix_inv_products_tenant_id_code", "tenant_id", "code",
                  unique=True, postgresql_where=text("deleted_at IS NULL")),
        )
        code: Mapped[str] = mapped_column(String(64))

    class ProductPrice(BaseRecord, Base):
        __tablename__ = "inv_product_prices"
        __table_args__ = (
            tenant_fk("inv_products", "product_id", ondelete="CASCADE"),  # FK komposit
            Index("ix_inv_product_prices_tenant_id_product_id", "tenant_id", "product_id"),
        )
        product_id: Mapped[UUID] = tenant_fk_column()

Unique `(tenant_id, id)` (`uq_<tabel>_tenant_id_id`) dipasang otomatis pada setiap tabel BaseRecord
sebagai target FK komposit; FK antar-tabel bisnis wajib komposit `(tenant_id, x_id) →
(tenant_id, id)` agar relasi tak pernah melintasi tenant (ADR 0004 §4a, dijaga meta-test).

Tidak ada indeks otomatis (hindari indeks ganda): tiap model wajib punya ≥ 1 indeks berawalan
`tenant_id` dan unique parsial `(tenant_id, …) WHERE deleted_at IS NULL`; migrasinya wajib
memanggil `enable_tenant_rls()` (dijaga meta-test). Tabel global (tenant, identitas user,
platform) tidak memakai BaseRecord/RLS. `company_id` → mixin `CompanyScoped` (P1-02).
"""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Table,
    UniqueConstraint,
    Uuid,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Mapper, declared_attr, mapped_column

from app.core.ids import uuid7
from app.core.tenancy.context import current_user_id, require_tenant_id

# Kolom yang hanya boleh diisi framework, tidak pernah dari input/schema request.
SYSTEM_COLUMNS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "deleted_at",
        "deleted_by",
        "delete_batch_id",
        "version",
    }
)

# Urutan kolom DDL: id & tenant_id di depan, kolom standar lain di belakang kolom model.
_HEAD = -100
_TAIL = 1000


class BaseRecord:
    """Mixin kolom standar: id UUIDv7, tenant, audit ringkas, soft delete, versi, custom fields."""

    # Klien boleh mengirim id sendiri (offline T1, PRD §6.6).
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7, sort_order=_HEAD)
    # Selalu dari konteks; tanpa konteks INSERT gagal (TenantContextMissing).
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, nullable=False, default=require_tenant_id, sort_order=_HEAD
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), sort_order=_TAIL
    )
    created_by: Mapped[UUID | None] = mapped_column(Uuid, default=current_user_id, sort_order=_TAIL)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        sort_order=_TAIL,
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        Uuid, default=current_user_id, onupdate=current_user_id, sort_order=_TAIL
    )

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), sort_order=_TAIL)
    deleted_by: Mapped[UUID | None] = mapped_column(Uuid, sort_order=_TAIL)
    delete_batch_id: Mapped[UUID | None] = mapped_column(Uuid, sort_order=_TAIL)

    # Optimistic locking: naik di setiap UPDATE (termasuk soft delete) → terlihat oleh sinkron.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1"), sort_order=_TAIL
    )
    # Ubah dengan meng-assign dict baru (tanpa MutableDict): mutasi in-place tidak terdeteksi.
    custom_fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), sort_order=_TAIL
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {
            "version_id_col": cls.__table__.c.version,  # type: ignore[attr-defined]
            # Wajib di async: nilai server (created_at, version, …) diambil lewat RETURNING,
            # bukan lazy load sesudah flush (MissingGreenlet).
            "eager_defaults": True,
        }


def tenant_unique_name(table_name: str) -> str:
    return f"uq_{table_name}_tenant_id_id"


@event.listens_for(BaseRecord, "instrument_class", propagate=True)
def _add_tenant_id_unique(mapper: Mapper[Any], class_: type) -> None:
    """Pasang UNIQUE `(tenant_id, id)` (target FK komposit) tanpa mengganggu `__table_args__`."""
    table = cast(Table, mapper.local_table)
    name = tenant_unique_name(table.name)
    if any(c.name == name for c in table.constraints):
        return
    table.append_constraint(UniqueConstraint("tenant_id", "id", name=name))


_ALLOWED_ONDELETE = ("CASCADE", "RESTRICT", "NO ACTION")


def tenant_fk(
    parent_table: str, column: str, *, ondelete: str | None = None, name: str | None = None
) -> ForeignKeyConstraint:
    """FK komposit `(tenant_id, <column>) → (<parent_table>.tenant_id, <parent_table>.id)`.

    Taruh di `__table_args__`; kolomnya dibuat dengan `tenant_fk_column()`. Dengan begitu DB
    menolak relasi lintas tenant (FK/unique PostgreSQL melewati RLS). Kolom FK nullable: pakai
    `tenant_fk_column(nullable=True)` — MATCH SIMPLE melewati FK bila salah satu kolom NULL.

    `ondelete` hanya `CASCADE`/`RESTRICT`/`NO ACTION`: `SET NULL`/`SET DEFAULT` ikut mengubah
    `tenant_id` (NOT NULL) sehingga delete induk gagal. Untuk "lepas relasi", kosongkan kolom FK
    di service sebelum menghapus induk.
    """
    if ondelete is not None and ondelete.upper() not in _ALLOWED_ONDELETE:
        raise ValueError(
            f"tenant_fk ondelete={ondelete!r} not allowed; use one of {_ALLOWED_ONDELETE}"
        )
    return ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{parent_table}.tenant_id", f"{parent_table}.id"],
        ondelete=ondelete,
        name=name,
    )


def tenant_fk_column(*, nullable: bool = False) -> Mapped[UUID]:
    """Kolom UUID untuk `tenant_fk` (tanpa `ForeignKey` sendiri: constraint ada di table args)."""
    return mapped_column(Uuid, nullable=nullable)
