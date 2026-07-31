from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.categories.repository import CategoryRepository
from app.modules.categories.schemas import CategoryCreate, CategoryUpdate
from app.modules.products.repository import ProductRepository


class CategoryService:
    def __init__(self, repo: CategoryRepository, products: ProductRepository):
        self.repo = repo
        self.products = products

    async def list_all(self, kind: str | None = None) -> list[dict]:
        # Categories written before the plant catalogue have no kind, so "pet"
        # means "not explicitly a plant" — same rule the product filters use.
        filter_ = {}
        if kind:
            filter_ = {"kind": "plant"} if kind == "plant" else {"kind": {"$ne": "plant"}}
        return await self.repo.find_many(filter_, sort=[("name", 1)], limit=200)

    async def get_by_id(self, category_id: str) -> dict:
        category = await self.repo.find_by_id(category_id)
        if not category:
            raise NotFoundError("Category not found")
        return category

    async def create(self, payload: CategoryCreate) -> dict:
        existing = await self.repo.find_by_slug(payload.slug)
        if existing:
            raise ConflictError("Category slug already exists")
        return await self.repo.insert(payload.model_dump())

    async def update(self, category_id: str, payload: CategoryUpdate) -> dict:
        update = {k: v for k, v in payload.model_dump().items() if v is not None}
        category = await self.repo.update_by_id(category_id, update)
        if not category:
            raise NotFoundError("Category not found")
        if "name" in update:
            await self.products.update_category_name(category_id, update["name"])
        return category

    async def delete(self, category_id: str) -> None:
        await self.get_by_id(category_id)
        in_use = await self.products.count({"category_id": category_id})
        if in_use > 0:
            raise ForbiddenError("Cannot delete a category that still has products")
        await self.repo.delete_by_id(category_id)
