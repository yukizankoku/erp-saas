"""Unit test validator & schema modul org (tanpa database)."""

import pytest
from pydantic import ValidationError

from app.modules.org.schemas import (
    BranchUpdate,
    CompanyCreate,
    CompanyUpdate,
    WarehouseUpdate,
    normalize_npwp,
    validate_currency,
    validate_timezone,
)


@pytest.mark.parametrize(
    ("raw", "digits"),
    [
        ("01.234.567.8-901.000", "012345678901000"),  # 15 digit berformat
        ("012345678901000", "012345678901000"),
        ("0123456789012345", "0123456789012345"),  # 16 digit (NIK/NPWP baru)
        (" 01 234 567 8 901 000 ", "012345678901000"),
        (None, None),
    ],
)
def test_npwp_normalised(raw: str | None, digits: str | None) -> None:
    assert normalize_npwp(raw) == digits


@pytest.mark.parametrize(
    "raw", ["", "12345", "01234567890100", "01234567890123456", "01.234.567.8-901.00A", "٠١٢٣"]
)
def test_npwp_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="NPWP"):
        normalize_npwp(raw)


def test_timezone_validation() -> None:
    assert validate_timezone("Asia/Jakarta") == "Asia/Jakarta"
    for bad in ("Mars/Olympus", "", "../etc/passwd", "Asia"):
        with pytest.raises(ValueError, match="timezone"):
            validate_timezone(bad)


def test_currency_validation() -> None:
    assert validate_currency(" idr ") == "IDR"
    for bad in ("ID", "IDRR", "I1R", ""):
        with pytest.raises(ValueError, match="currency"):
            validate_currency(bad)


def test_company_create_defaults_and_normalisation() -> None:
    company = CompanyCreate(code="HQ", legal_name="PT Contoh", npwp="01.234.567.8-901.000")
    assert company.npwp == "012345678901000"
    assert (company.functional_currency, company.timezone) == ("IDR", "Asia/Jakarta")
    assert (company.default_language, company.fiscal_year_start_month) == ("id", 1)


@pytest.mark.parametrize(
    "bad",
    [
        {"npwp": "123"},
        {"timezone": "Nowhere/City"},
        {"functional_currency": "rupiah"},
        {"default_language": "fr"},
        {"fiscal_year_start_month": 13},
        {"fiscal_year_start_month": 0},
    ],
)
def test_company_create_rejects_invalid(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CompanyCreate(code="HQ", legal_name="PT Contoh", **bad)  # type: ignore[arg-type]


def test_update_schemas_only_allow_null_on_nullable_fields() -> None:
    assert CompanyUpdate(npwp=None).model_dump(exclude_unset=True) == {"npwp": None}
    with pytest.raises(ValidationError):
        CompanyUpdate(legal_name=None)
    assert BranchUpdate(timezone=None).model_dump(exclude_unset=True) == {"timezone": None}
    with pytest.raises(ValidationError):
        BranchUpdate(name=None)
    assert WarehouseUpdate(branch_id=None).model_dump(exclude_unset=True) == {"branch_id": None}
    with pytest.raises(ValidationError):
        WarehouseUpdate(code=None)
