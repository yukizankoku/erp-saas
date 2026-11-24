import asyncio
from uuid import UUID

import pytest

from app.core.ids import uuid7
from app.core.tenancy.context import (
    TenantContext,
    TenantContextMissing,
    current_user_id,
    get_context,
    require_tenant_id,
    tenant_context,
)


def test_no_context_by_default_fails_closed() -> None:
    assert get_context() is None
    assert current_user_id() is None
    with pytest.raises(TenantContextMissing):
        require_tenant_id()


def test_tenant_context_sets_and_resets() -> None:
    tenant, user, company = uuid7(), uuid7(), uuid7()
    with tenant_context(tenant, user_id=user, company_id=company) as ctx:
        assert get_context() == ctx == TenantContext(tenant, user, company)
        assert require_tenant_id() == tenant
        assert current_user_id() == user
    assert get_context() is None


def test_nested_context_restores_outer() -> None:
    outer, inner = uuid7(), uuid7()
    with tenant_context(outer):
        with tenant_context(inner):
            assert require_tenant_id() == inner
        assert require_tenant_id() == outer
    assert get_context() is None


def test_context_reset_even_on_error() -> None:
    with pytest.raises(RuntimeError), tenant_context(uuid7()):
        raise RuntimeError("boom")
    assert get_context() is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tenant_id": "0190f0a0-0000-7000-8000-000000000000"},
        {"tenant_id": None},
        {"tenant_id": UUID(int=1), "user_id": "x"},
        {"tenant_id": UUID(int=1), "company_id": 5},
    ],
)
def test_context_rejects_non_uuid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(TypeError):
        TenantContext(**kwargs)  # type: ignore[arg-type]


def test_context_is_frozen() -> None:
    ctx = TenantContext(uuid7())
    with pytest.raises(AttributeError):
        ctx.tenant_id = uuid7()  # type: ignore[misc]


async def test_context_is_isolated_between_concurrent_tasks() -> None:
    seen: dict[str, UUID] = {}

    async def worker(name: str, tenant: UUID) -> None:
        with tenant_context(tenant):
            await asyncio.sleep(0.01)
            seen[name] = require_tenant_id()

    a, b = uuid7(), uuid7()
    await asyncio.gather(worker("a", a), worker("b", b))
    assert seen == {"a": a, "b": b}
    assert get_context() is None
