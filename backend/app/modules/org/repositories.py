"""Repository modul org. Query selalu dari `self.select()` (scope tenant + belum dihapus)."""

from uuid import UUID

from app.core.tenancy.session import ensure_tenant_bound
from app.framework.repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page, TenantRepository
from app.modules.org.models import Branch, Company, Warehouse


class CompanyRepository(TenantRepository[Company]):
    model = Company


class _CompanyChildRepository[T: (Branch, Warehouse)](TenantRepository[T]):
    async def list_by_company(
        self, company_id: UUID, *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE
    ) -> Page[T]:
        if page < 1:
            raise ValueError("page must be >= 1")
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        await ensure_tenant_bound(self.session)
        stmt = (
            self.select()
            .where(self.model.company_id == company_id)
            .order_by(self.model.code, self.model.id)
            .offset((page - 1) * page_size)
            .limit(page_size + 1)
        )
        rows = (await self.session.scalars(stmt)).all()
        return Page(
            items=rows[:page_size], page=page, page_size=page_size, has_more=len(rows) > page_size
        )

    async def has_any_for_company(self, company_id: UUID) -> bool:
        await ensure_tenant_bound(self.session)
        stmt = self.select().where(self.model.company_id == company_id).limit(1)
        return (await self.session.scalars(stmt)).first() is not None


class BranchRepository(_CompanyChildRepository[Branch]):
    model = Branch


class WarehouseRepository(_CompanyChildRepository[Warehouse]):
    model = Warehouse

    async def has_any_for_branch(self, branch_id: UUID) -> bool:
        await ensure_tenant_bound(self.session)
        stmt = self.select().where(Warehouse.branch_id == branch_id).limit(1)
        return (await self.session.scalars(stmt)).first() is not None
