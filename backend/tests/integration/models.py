"""Model khusus test integrasi (metadata terpisah dari `app.core.db.Base`)."""

from uuid import UUID

from sqlalchemy import ForeignKey, Index, MetaData, String, Uuid, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.db import NAMING_CONVENTION
from app.framework.record import BaseRecord
from app.framework.repository import TenantRepository


class IntegrationBase(DeclarativeBase):
    # Metadata sendiri: tabel uji tidak ikut autogenerate / `alembic check`.
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Item(BaseRecord, IntegrationBase):
    __tablename__ = "rls_test_items"
    __table_args__ = (
        Index("ix_rls_test_items_tenant_id_name", "tenant_id", "name"),
        Index(
            "uq_rls_test_items_tenant_id_code",
            "tenant_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(100))
    code: Mapped[str | None] = mapped_column(String(50))


class ItemChild(BaseRecord, IntegrationBase):
    """Anak `Item` untuk menguji lazy/selectin load lintas tenant."""

    __tablename__ = "rls_test_item_children"
    __table_args__ = (Index("ix_rls_test_item_children_tenant_id_item_id", "tenant_id", "item_id"),)

    item_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("rls_test_items.id"))
    label: Mapped[str] = mapped_column(String(100))


Item.children = relationship(ItemChild, lazy="select")  # type: ignore[attr-defined]


class ItemRepository(TenantRepository[Item]):
    model = Item
