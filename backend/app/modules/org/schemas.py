"""Schema Pydantic modul org (dipakai controller di bagian B) + validator murni."""

import re
from datetime import datetime
from typing import Any, ClassVar, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_NPWP_SEPARATORS = re.compile(r"[.\-\s]")
_CURRENCY = re.compile(r"[A-Z]{3}")


def normalize_npwp(value: str | None) -> str | None:
    """NPWP 15/16 digit; titik/strip/spasi diterima di input, disimpan sebagai digit saja."""
    if value is None:
        return None
    digits = _NPWP_SEPARATORS.sub("", value)
    if not (digits.isascii() and digits.isdigit() and len(digits) in (15, 16)):
        raise ValueError("NPWP must be 15 or 16 digits")
    return digits


def validate_timezone(value: str | None) -> str | None:
    """Nama zona IANA yang dikenal `zoneinfo`."""
    if value is None:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError(f"unknown IANA timezone: {value!r}") from None
    return value


def validate_currency(value: str) -> str:
    """Format ISO 4217 (3 huruf kapital). Daftar kode resmi tidak dicek (format saja)."""
    code = value.strip().upper()
    if not _CURRENCY.fullmatch(code):
        raise ValueError("currency must be a 3-letter ISO 4217 code")
    return code


class _Validated(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("npwp", check_fields=False)
    @classmethod
    def _npwp(cls, v: str | None) -> str | None:
        return normalize_npwp(v)

    @field_validator("timezone", check_fields=False)
    @classmethod
    def _timezone(cls, v: str | None) -> str | None:
        return validate_timezone(v)

    @field_validator("functional_currency", check_fields=False)
    @classmethod
    def _currency(cls, v: str | None) -> str | None:
        return None if v is None else validate_currency(v)


class _Update(_Validated):
    """Field yang tidak dikirim tidak diubah; `null` hanya sah untuk field di `nullable`."""

    nullable: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _no_null_for_required(self) -> Self:
        for name in self.model_fields_set - self.nullable:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class CompanyCreate(_Validated):
    code: str = Field(min_length=1, max_length=32)
    legal_name: str = Field(min_length=1, max_length=200)
    npwp: str | None = None
    address: str = ""
    logo_key: str | None = Field(default=None, max_length=512)
    functional_currency: str = "IDR"
    timezone: str = "Asia/Jakarta"
    default_language: Literal["id", "en"] = "id"
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)


class CompanyUpdate(_Update):
    code: str | None = Field(default=None, min_length=1, max_length=32)
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)
    npwp: str | None = None
    address: str | None = None
    logo_key: str | None = Field(default=None, max_length=512)
    functional_currency: str | None = None
    timezone: str | None = None
    default_language: Literal["id", "en"] | None = None
    fiscal_year_start_month: int | None = Field(default=None, ge=1, le=12)

    nullable = frozenset({"npwp", "logo_key"})


class BranchCreate(_Validated):
    company_id: UUID
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    address: str = ""
    timezone: str | None = None


class BranchUpdate(_Update):
    nullable = frozenset({"timezone"})

    code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = None
    timezone: str | None = None


class WarehouseCreate(_Validated):
    company_id: UUID
    branch_id: UUID | None = None
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)


class WarehouseUpdate(_Update):
    nullable = frozenset({"branch_id"})

    branch_id: UUID | None = None
    code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=200)


class _Read(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    created_at: datetime
    updated_at: datetime


class CompanyRead(_Read):
    code: str
    legal_name: str
    npwp: str | None
    address: str
    logo_key: str | None
    functional_currency: str
    timezone: str
    default_language: str
    fiscal_year_start_month: int
    settings: dict[str, Any]


class BranchRead(_Read):
    company_id: UUID
    code: str
    name: str
    address: str
    timezone: str | None


class WarehouseRead(_Read):
    company_id: UUID
    branch_id: UUID | None
    code: str
    name: str
