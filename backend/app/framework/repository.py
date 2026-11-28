"""`TenantRepository[T]`: akses data model `BaseRecord` yang selalu ter-scope tenant (ADR 0004 §7).

Lapis 1 isolasi tenant (filter aplikasi; kini juga otomatis di ORM lewat `orm_filter`, ADR 0004
§7a); lapis 2 = RLS PostgreSQL. Setiap metode memanggil `ensure_tenant_bound()` lalu menyusun query
dari `self.select()` (sudah berisi `tenant_id = konteks AND deleted_at IS NULL`) — subclass tetap
wajib melakukan hal yang sama (statement Core/`text()` tidak difilter ORM), jangan
`sa.select(Model)` langsung. Record tenant lain = tidak ada (None / 404, bukan 403).

Repository hanya `flush()`; commit/rollback milik dependency sesi atau service. Setelah
`VersionConflict` dari flush, sesi wajib di-rollback sebelum dipakai lagi. Pengecualian: `create`
dengan `id` yang sudah dipakai (PK global, tenant mana pun) me-rollback transaksi sesi sendiri
(kecuali di dalam savepoint pemanggil — savepoint itu yang dibatalkan pemanggil) lalu raise
`RecordConflict` (409, pesan identik untuk tenant sendiri/lain). FK violation (23503) dari input
`create`/`update` → `RecordReferenceInvalid` (422, identik untuk target tak ada / tenant lain),
diisolasi savepoint sehingga tulisan lain di transaksi tetap utuh.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.ids import uuid7
from app.core.tenancy.context import current_user_id, require_tenant_id
from app.core.tenancy.session import ensure_tenant_bound
from app.framework.errors import (
    RecordConflict,
    RecordNotFound,
    RecordReferenceInvalid,
    VersionConflict,
)
from app.framework.record import SYSTEM_COLUMNS, BaseRecord

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: Sequence[T]
    page: int
    page_size: int
    has_more: bool  # tanpa COUNT(*) — mahal di tabel besar


class TenantRepository[T: BaseRecord]:
    """Subclass: `class ProductRepository(TenantRepository[Product]): model = Product`."""

    model: type[T]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def select(self) -> sa.Select[T]:
        """Query dasar: hanya baris tenant konteks yang belum dihapus."""
        model = self.model
        return sa.select(model).where(
            model.tenant_id == require_tenant_id(),
            model.deleted_at.is_(None),
        )

    async def get(self, id: UUID) -> T | None:
        await ensure_tenant_bound(self.session)
        result = await self.session.execute(self.select().where(self.model.id == id))
        return result.scalar_one_or_none()

    async def list(self, *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> Page[T]:
        if page < 1:
            raise ValueError("page must be >= 1")
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        await ensure_tenant_bound(self.session)
        stmt = (
            self.select()
            .order_by(self.model.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size + 1)
        )
        rows = (await self.session.scalars(stmt)).all()
        return Page(
            items=rows[:page_size],
            page=page,
            page_size=page_size,
            has_more=len(rows) > page_size,
        )

    async def create(self, values: Mapping[str, Any]) -> T:
        """Buat record; `id` boleh dari klien (offline), kolom sistem selalu dari framework."""
        self._check_values(values, allow_id=True)
        tenant_id = await ensure_tenant_bound(self.session)
        record = self.model()
        for key, value in values.items():
            setattr(record, key, value)
        record.tenant_id = tenant_id
        # Flush tulisan tertunda milik pemanggil di luar savepoint: FK error-nya bukan input ini.
        await self.session.flush()
        if "id" not in values:
            # Id dibuat server (UUIDv7) → tidak bisa bentrok; tanpa savepoint tambahan.
            try:
                async with self.session.begin_nested():  # savepoint: tulisan lain tetap utuh
                    self.session.add(record)
                    await self.session.flush()
            except IntegrityError as exc:
                self._raise_if_reference_violation(exc)
                raise
            return record
        try:
            # Id dari klien (offline) bisa bentrok: insert diisolasi dalam savepoint agar hanya
            # insert ini yang batal; tulisan lain di transaksi pemanggil tetap utuh (atomisitas).
            async with self.session.begin_nested():
                self.session.add(record)
                await self.session.flush()
        except IntegrityError as exc:
            self._raise_if_reference_violation(exc)
            if not self._is_primary_key_violation(exc):
                raise
            # Pesan sengaja sama untuk id milik tenant sendiri maupun tenant lain (tanpa oracle).
            raise RecordConflict() from exc
        return record

    async def update(self, id: UUID, values: Mapping[str, Any], *, expected_version: int) -> T:
        """Ubah record bila versinya masih `expected_version` (If-Match); versi lalu naik 1."""
        self._check_values(values, allow_id=False)
        record = await self._get_for_write(id, expected_version)
        await self.session.flush()  # tulisan tertunda pemanggil: IntegrityError apa adanya
        try:
            # Savepoint dibuka sebelum atribut diubah (begin_nested mem-flush perubahan tertunda).
            async with self.session.begin_nested():
                for key, value in values.items():
                    setattr(record, key, value)
                await self._flush()
        except RecordReferenceInvalid:
            # Buang perubahan tertunda agar autoflush berikutnya tidak mengulang UPDATE yang gagal.
            self.session.expire(record)
            raise
        return record

    async def soft_delete(
        self, id: UUID, *, expected_version: int, batch_id: UUID | None = None
    ) -> UUID:
        """Tandai terhapus; kembalikan `delete_batch_id` (untuk restore massal, PRD §7.0)."""
        record = await self._get_for_write(id, expected_version)
        batch = batch_id or uuid7()
        record.deleted_at = datetime.now(UTC)
        record.deleted_by = current_user_id()
        record.delete_batch_id = batch
        await self._flush()  # version ikut naik → perubahan terlihat oleh sinkron (§6.6)
        return batch

    async def _get_for_write(self, id: UUID, expected_version: int) -> T:
        await ensure_tenant_bound(self.session)
        # populate_existing: bandingkan dengan versi terbaru di DB, bukan salinan lama di sesi.
        stmt = self.select().where(self.model.id == id).execution_options(populate_existing=True)
        record = (await self.session.execute(stmt)).scalar_one_or_none()
        if record is None:
            raise RecordNotFound()
        # version_id_col hanya membandingkan dengan versi yang termuat, jadi If-Match dicek di sini;
        # balapan sesudah pengecekan ini tertangkap sebagai StaleDataError saat flush.
        if record.version != expected_version:
            raise VersionConflict()
        return record

    async def _flush(self) -> None:
        try:
            await self.session.flush()
        except StaleDataError as exc:
            raise VersionConflict() from exc
        except IntegrityError as exc:
            self._raise_if_reference_violation(exc)
            raise

    @staticmethod
    def _raise_if_reference_violation(exc: IntegrityError) -> None:
        """FK violation (23503) → `RecordReferenceInvalid`, identik untuk tak ada / tenant lain."""
        if getattr(exc.orig, "sqlstate", None) == FOREIGN_KEY_VIOLATION:
            raise RecordReferenceInvalid() from exc

    def _is_primary_key_violation(self, exc: IntegrityError) -> bool:
        """Unique violation pada constraint PK tabel model (`pk_<tabel>`, naming convention)."""
        orig = exc.orig
        if getattr(orig, "sqlstate", None) != UNIQUE_VIOLATION:
            return False
        # asyncpg: `driver_exception.constraint_name`; psycopg: `diag.constraint_name`.
        driver = getattr(orig, "driver_exception", None) or getattr(orig, "__cause__", None)
        constraint = getattr(driver, "constraint_name", None) or getattr(
            getattr(orig, "diag", None), "constraint_name", None
        )
        table = sa.inspect(self.model).local_table
        return constraint is not None and constraint == (
            table.primary_key.name or f"pk_{table.name}"
        )

    def _check_values(self, values: Mapping[str, Any], *, allow_id: bool) -> None:
        columns = {attr.key for attr in sa.inspect(self.model).column_attrs}
        for key in values:
            if key in SYSTEM_COLUMNS or (key == "id" and not allow_id):
                raise ValueError(f"system column {key!r} cannot be set through the repository")
            if key not in columns:
                raise ValueError(f"unknown column {key!r} for {self.model.__name__}")
