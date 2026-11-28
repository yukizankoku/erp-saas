"""Service modul org: validasi domain, konsistensi company/branch/warehouse, pengaturan company.

Input tervalidasi oleh schema (NPWP, zona waktu, mata uang). Service tidak commit: transaksi milik
pemanggil (dependency sesi). Setelah error dari service, sesi wajib di-rollback oleh pemanggil.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.errors import CodeConflict, RecordInUse, RecordNotFound, RecordReferenceInvalid
from app.framework.repository import UNIQUE_VIOLATION, Page
from app.modules.org.models import Branch, Company, Tenant, Warehouse
from app.modules.org.repositories import BranchRepository, CompanyRepository, WarehouseRepository
from app.modules.org.schemas import (
    BranchCreate,
    BranchUpdate,
    CompanyCreate,
    CompanyUpdate,
    WarehouseCreate,
    WarehouseUpdate,
)


async def create_tenant(
    session: AsyncSession, *, name: str, slug: str, id: UUID | None = None
) -> Tenant:
    """Hanya kode platform (signup #26, seed, test); role runtime tidak boleh menulis `tenants`."""
    tenant = Tenant(name=name, slug=slug)
    if id is not None:
        tenant.id = id
    session.add(tenant)
    await session.flush()
    return tenant


def _is_unique_violation(exc: IntegrityError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION


class CompanyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.companies = CompanyRepository(session)
        self.branches = BranchRepository(session)
        self.warehouses = WarehouseRepository(session)

    async def get(self, company_id: UUID) -> Company:
        company = await self.companies.get(company_id)
        if company is None:
            raise RecordNotFound()
        return company

    async def list(self, *, page: int = 1, page_size: int = 50) -> Page[Company]:
        return await self.companies.list(page=page, page_size=page_size)

    async def create(self, data: CompanyCreate) -> Company:
        try:
            return await self.companies.create(data.model_dump())
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise CodeConflict() from exc
            raise

    async def update(
        self, company_id: UUID, data: CompanyUpdate, *, expected_version: int
    ) -> Company:
        try:
            return await self.companies.update(
                company_id, data.model_dump(exclude_unset=True), expected_version=expected_version
            )
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise CodeConflict() from exc
            raise

    async def soft_delete(self, company_id: UUID, *, expected_version: int) -> UUID:
        if await self.branches.has_any_for_company(
            company_id
        ) or await self.warehouses.has_any_for_company(company_id):
            raise RecordInUse()
        return await self.companies.soft_delete(company_id, expected_version=expected_version)

    async def get_settings(self, company_id: UUID) -> dict[str, Any]:
        return dict((await self.get(company_id)).settings)

    async def update_settings(
        self, company_id: UUID, patch: dict[str, Any], *, expected_version: int
    ) -> dict[str, Any]:
        """Merge dangkal (kunci level atas di `patch` menimpa); selalu meng-assign dict baru."""
        company = await self.get(company_id)
        merged = {**company.settings, **patch}
        updated = await self.companies.update(
            company_id, {"settings": merged}, expected_version=expected_version
        )
        return dict(updated.settings)


class BranchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.companies = CompanyRepository(session)
        self.branches = BranchRepository(session)
        self.warehouses = WarehouseRepository(session)

    async def get(self, branch_id: UUID) -> Branch:
        branch = await self.branches.get(branch_id)
        if branch is None:
            raise RecordNotFound()
        return branch

    async def list_by_company(
        self, company_id: UUID, *, page: int = 1, page_size: int = 50
    ) -> Page[Branch]:
        return await self.branches.list_by_company(company_id, page=page, page_size=page_size)

    async def create(self, data: BranchCreate) -> Branch:
        if await self.companies.get(data.company_id) is None:
            raise RecordReferenceInvalid()
        try:
            return await self.branches.create(data.model_dump())
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise CodeConflict() from exc
            raise

    async def update(self, branch_id: UUID, data: BranchUpdate, *, expected_version: int) -> Branch:
        try:
            return await self.branches.update(
                branch_id, data.model_dump(exclude_unset=True), expected_version=expected_version
            )
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise CodeConflict() from exc
            raise

    async def soft_delete(self, branch_id: UUID, *, expected_version: int) -> UUID:
        if await self.warehouses.has_any_for_branch(branch_id):
            raise RecordInUse()
        return await self.branches.soft_delete(branch_id, expected_version=expected_version)

    async def effective_timezone(self, branch: Branch) -> str:
        """Zona waktu cabang; bila kosong, ikut company."""
        if branch.timezone:
            return branch.timezone
        company = await self.companies.get(branch.company_id)
        if company is None:
            raise RecordNotFound()
        return company.timezone


class WarehouseService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.companies = CompanyRepository(session)
        self.branches = BranchRepository(session)
        self.warehouses = WarehouseRepository(session)

    async def get(self, warehouse_id: UUID) -> Warehouse:
        warehouse = await self.warehouses.get(warehouse_id)
        if warehouse is None:
            raise RecordNotFound()
        return warehouse

    async def list_by_company(
        self, company_id: UUID, *, page: int = 1, page_size: int = 50
    ) -> Page[Warehouse]:
        return await self.warehouses.list_by_company(company_id, page=page, page_size=page_size)

    async def _check_branch(self, company_id: UUID, branch_id: UUID | None) -> None:
        """Cabang harus ada (aktif) dan milik company yang sama; DB (FK 3 kolom) sabuk kedua."""
        if branch_id is None:
            return
        branch = await self.branches.get(branch_id)
        if branch is None or branch.company_id != company_id:
            raise RecordReferenceInvalid()

    async def create(self, data: WarehouseCreate) -> Warehouse:
        if await self.companies.get(data.company_id) is None:
            raise RecordReferenceInvalid()
        await self._check_branch(data.company_id, data.branch_id)
        try:
            return await self.warehouses.create(data.model_dump())
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise CodeConflict() from exc
            raise

    async def update(
        self, warehouse_id: UUID, data: WarehouseUpdate, *, expected_version: int
    ) -> Warehouse:
        values = data.model_dump(exclude_unset=True)
        if "branch_id" in values:
            warehouse = await self.get(warehouse_id)
            await self._check_branch(warehouse.company_id, values["branch_id"])
        try:
            return await self.warehouses.update(
                warehouse_id, values, expected_version=expected_version
            )
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise CodeConflict() from exc
            raise

    async def soft_delete(self, warehouse_id: UUID, *, expected_version: int) -> UUID:
        return await self.warehouses.soft_delete(warehouse_id, expected_version=expected_version)
