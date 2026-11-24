"""Unit test Record Framework tanpa database (perilaku DB ada di tests/integration)."""

from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.ids import uuid7
from app.core.tenancy.context import TenantContextMissing, tenant_context
from app.framework.errors import (
    RecordConflict,
    RecordNotFound,
    VersionConflict,
    preferred_language,
)
from app.framework.record import SYSTEM_COLUMNS, BaseRecord
from app.framework.repository import MAX_PAGE_SIZE, TenantRepository
from app.main import create_app


class _Base(DeclarativeBase):
    pass


class Widget(BaseRecord, _Base):
    __tablename__ = "widgets"
    name: Mapped[str] = mapped_column(String(50))


class WidgetRepository(TenantRepository[Widget]):
    model = Widget


def test_base_record_columns_and_mapper_args() -> None:
    table = _Base.metadata.tables["widgets"]
    assert [c.name for c in table.c] == [
        "id",
        "tenant_id",
        "name",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "deleted_at",
        "deleted_by",
        "delete_batch_id",
        "version",
        "custom_fields",
    ]
    mapper = inspect(Widget)
    assert mapper.version_id_col is table.c.version
    assert mapper.eager_defaults is True
    assert not table.indexes  # tanpa indeks otomatis: tiap model mendefinisikan sendiri
    for name in ("tenant_id", "created_at", "updated_at", "version", "custom_fields"):
        assert table.c[name].nullable is False, name
    for name in ("created_by", "updated_by", "deleted_at", "deleted_by", "delete_batch_id"):
        assert table.c[name].nullable is True, name
    for name in ("created_at", "updated_at", "deleted_at"):
        assert table.c[name].type.timezone is True, name  # type: ignore[attr-defined]
    assert {c.name for c in table.c} - {"id", "name", "custom_fields"} == SYSTEM_COLUMNS


def test_tenant_id_default_requires_context() -> None:
    default = Widget.__table__.c.tenant_id.default
    assert default is not None
    with pytest.raises(TenantContextMissing):
        default.arg(None)
    tenant = uuid7()
    with tenant_context(tenant):
        assert default.arg(None) == tenant


def test_id_default_is_uuid7() -> None:
    default = Widget.__table__.c.id.default
    assert default is not None
    value = default.arg(None)
    assert isinstance(value, UUID)
    assert value.version == 7


@pytest.mark.parametrize("column", sorted(SYSTEM_COLUMNS))
async def test_repository_rejects_system_columns(column: str) -> None:
    repo = WidgetRepository(AsyncSession())
    with pytest.raises(ValueError, match="system column"):
        await repo.create({"name": "x", column: None})
    with pytest.raises(ValueError, match="system column"):
        await repo.update(uuid7(), {column: None}, expected_version=1)


async def test_repository_rejects_id_on_update_and_unknown_columns() -> None:
    repo = WidgetRepository(AsyncSession())
    with pytest.raises(ValueError, match="system column 'id'"):
        await repo.update(uuid7(), {"id": uuid7()}, expected_version=1)
    with pytest.raises(ValueError, match="unknown column"):
        await repo.create({"name": "x", "colour": "red"})


@pytest.mark.parametrize(("page", "page_size"), [(0, 10), (-1, 10), (1, 0), (1, MAX_PAGE_SIZE + 1)])
async def test_list_validates_paging(page: int, page_size: int) -> None:
    repo = WidgetRepository(AsyncSession())
    with tenant_context(uuid7()), pytest.raises(ValueError):
        await repo.list(page=page, page_size=page_size)


def test_select_requires_context_and_filters_tenant_and_deleted() -> None:
    repo = WidgetRepository(AsyncSession())
    with pytest.raises(TenantContextMissing):
        repo.select()
    with tenant_context(uuid7()):
        sql = str(repo.select())
    assert "widgets.tenant_id = :tenant_id_1" in sql
    assert "widgets.deleted_at IS NULL" in sql


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "id"),
        ("", "id"),
        ("en", "en"),
        ("en-US,en;q=0.9", "en"),
        ("id-ID,id;q=0.9,en;q=0.8", "id"),
        ("fr-FR, en;q=0.5, id;q=0.7", "id"),
        ("fr, de", "id"),
        ("en;q=0, id;q=0.1", "id"),
        ("en;q=abc, id;q=0.2", "id"),
    ],
)
def test_preferred_language(header: str | None, expected: str) -> None:
    assert preferred_language(header) == expected


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (VersionConflict(), 409, "record.version_conflict"),
        (RecordNotFound(), 404, "record.not_found"),
        (RecordConflict(), 409, "record.conflict"),
    ],
)
@pytest.mark.parametrize("lang", ["id", "en"])
async def test_record_errors_map_to_problem_details(
    exc: Exception, status: int, code: str, lang: str
) -> None:
    app = create_app()

    @app.get("/_test/raise")
    async def _raise() -> dict[str, Any]:
        raise exc

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_test/raise", headers={"Accept-Language": lang})
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["content-language"] == lang
    body = response.json()
    assert body["status"] == status
    assert body["code"] == code
    assert body["title"] and body["detail"]
