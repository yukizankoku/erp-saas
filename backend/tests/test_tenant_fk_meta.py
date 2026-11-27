"""Meta-test: FK antar-tabel bisnis wajib komposit `(tenant_id, x_id)` (ADR 0004 §4a, #37)."""

from uuid import UUID

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, Table, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.db import NAMING_CONVENTION, Base
from app.framework.record import BaseRecord, tenant_fk, tenant_fk_column
from tests.fk_meta import check_tenant_fk_rules
from tests.integration.models import IntegrationBase


def test_app_metadata_follows_tenant_fk_rules() -> None:
    assert check_tenant_fk_rules(Base.metadata) == []


def test_integration_metadata_follows_tenant_fk_rules() -> None:
    assert "rls_test_item_children" in IntegrationBase.metadata.tables
    assert check_tenant_fk_rules(IntegrationBase.metadata) == []


def test_checker_rejects_bad_models_in_isolated_metadata() -> None:
    class Iso(DeclarativeBase):
        metadata = MetaData(naming_convention=NAMING_CONVENTION)

    class Parent(BaseRecord, Iso):
        __tablename__ = "iso_parents"

    class Bad(BaseRecord, Iso):
        __tablename__ = "iso_bad"
        parent_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("iso_parents.id"))

    class Good(BaseRecord, Iso):
        __tablename__ = "iso_good"
        __table_args__ = (tenant_fk("iso_parents", "parent_id"),)
        parent_id: Mapped[UUID] = tenant_fk_column()

    assert (Parent.__tablename__, Good.__tablename__) == ("iso_parents", "iso_good")
    problems = check_tenant_fk_rules(Iso.metadata)
    assert len(problems) == 1 and problems[0].startswith(
        "iso_bad: FK ke iso_parents bukan komposit"
    )

    # Tabel ber-tenant_id tanpa UNIQUE (tenant_id, id) juga ditolak.
    bare = MetaData()
    Table("bare", bare, Column("id", Uuid, primary_key=True), Column("tenant_id", Uuid))
    assert check_tenant_fk_rules(bare) == ["bare: tidak ada UNIQUE (tenant_id, id)"]


def test_unique_tenant_id_name_and_custom_table_args_preserved() -> None:
    table = IntegrationBase.metadata.tables["rls_test_items"]
    names = {c.name for c in table.constraints}
    assert "uq_rls_test_items_tenant_id_id" in names
    assert any(i.name == "ix_rls_test_items_tenant_id_name" for i in table.indexes)


@pytest.mark.parametrize("ondelete", ["SET NULL", "set default"])
def test_tenant_fk_rejects_ondelete_that_touches_tenant_id(ondelete: str) -> None:
    with pytest.raises(ValueError):
        tenant_fk("parents", "parent_id", ondelete=ondelete)
