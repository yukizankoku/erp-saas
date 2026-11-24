import pytest

from app.core.tenancy import rls
from app.core.tenancy.rls import drop_tenant_rls_sql, tenant_rls_sql, validate_table_name


def test_tenant_rls_sql_enables_forces_and_creates_policy() -> None:
    statements = tenant_rls_sql("sales_orders")
    assert statements == [
        "ALTER TABLE sales_orders ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE sales_orders FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation ON sales_orders "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    ]


def test_drop_tenant_rls_sql_reverses_in_order() -> None:
    assert drop_tenant_rls_sql("sales_orders") == [
        "DROP POLICY IF EXISTS tenant_isolation ON sales_orders",
        "ALTER TABLE sales_orders NO FORCE ROW LEVEL SECURITY",
        "ALTER TABLE sales_orders DISABLE ROW LEVEL SECURITY",
    ]


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Orders",
        "1orders",
        "_orders",
        "public.orders",
        'orders"',
        "orders; DROP TABLE x",
        "orders--",
        "orders\n",
        "ordérs",
        "a" * 64,
    ],
)
def test_invalid_table_names_are_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        validate_table_name(name)
    with pytest.raises(ValueError):
        tenant_rls_sql(name)
    with pytest.raises(ValueError):
        drop_tenant_rls_sql(name)


def test_valid_table_names() -> None:
    assert validate_table_name("a") == "a"
    assert validate_table_name("inv_stock_moves_2026") == "inv_stock_moves_2026"
    assert validate_table_name("a" * 63) == "a" * 63


def test_alembic_wrappers_execute_each_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[str] = []

    class FakeOp:
        @staticmethod
        def execute(statement: str) -> None:
            executed.append(statement)

    import alembic

    monkeypatch.setattr(alembic, "op", FakeOp)
    rls.enable_tenant_rls("sales_orders")
    rls.disable_tenant_rls("sales_orders")
    assert executed == tenant_rls_sql("sales_orders") + drop_tenant_rls_sql("sales_orders")
