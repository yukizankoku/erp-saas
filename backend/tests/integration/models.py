"""Model khusus test integrasi (metadata terpisah dari `app.core.db.Base`)."""

from sqlalchemy import Index, MetaData, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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


class ItemRepository(TenantRepository[Item]):
    model = Item
