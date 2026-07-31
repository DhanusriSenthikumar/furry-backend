from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.pagination import Pagination
from app.modules.categories.repository import CategoryRepository
from app.modules.products.repository import ProductRepository
from app.modules.products.schemas import PlantDetails, ProductCreate, ProductUpdate
from app.modules.stock_alerts.service import StockAlertService

SORT_MAP = {
    "price_asc": [("price", 1)],
    "price_desc": [("price", -1)],
    "rating": [("rating", -1)],
    "newest": [("_id", -1)],
    # Deepest saving first; the compare price is 0 on everything not on sale, so
    # full-price stock naturally sinks to the bottom.
    "discount": [("compare_at_price", -1)],
}


class ProductService:
    def __init__(
        self,
        repo: ProductRepository,
        categories: CategoryRepository,
        alerts: StockAlertService | None = None,
    ):
        self.repo = repo
        self.categories = categories
        # Only needed to tell waiting customers about a restock. Without it the
        # stock edit still lands; the waiting list just doesn't get flushed.
        self.alerts = alerts

    async def list_products(
        self,
        pagination: Pagination,
        category_id: str | None = None,
        pet_type: str | None = None,
        q: str | None = None,
        sort: str | None = None,
        brand: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        in_stock: bool = False,
        product_kind: str | None = None,
        plant_type: str | None = None,
        light_needs: str | None = None,
        water_needs: str | None = None,
        care_level: str | None = None,
        pot_included: bool = False,
        min_rating: float | None = None,
        on_sale: bool = False,
    ) -> list[dict]:
        filter_ = self.repo.build_filter(
            category_id=category_id,
            pet_type=pet_type,
            q=q,
            brand=brand,
            min_price=min_price,
            max_price=max_price,
            in_stock=in_stock,
            product_kind=product_kind,
            plant_type=plant_type,
            light_needs=light_needs,
            water_needs=water_needs,
            care_level=care_level,
            pot_included=pot_included,
            min_rating=min_rating,
            on_sale=on_sale,
        )
        return await self.repo.find_many(
            filter_, skip=pagination.skip, limit=pagination.page_size, sort=SORT_MAP.get(sort)
        )

    async def list_brands(self, product_kind: str | None = None) -> list[str]:
        return await self.repo.distinct_brands(product_kind)

    async def suggest(self, q: str, limit: int = 8, product_kind: str | None = None) -> list[dict]:
        if not q or not q.strip():
            return []
        return await self.repo.suggest(q.strip(), limit=limit, product_kind=product_kind)

    async def get_by_id(self, product_id: str) -> dict:
        product = await self.repo.find_by_id(product_id)
        if not product:
            raise NotFoundError("Product not found")
        return product

    async def get_by_slug(self, slug: str) -> dict:
        product = await self.repo.find_by_slug(slug)
        if not product:
            raise NotFoundError("Product not found")
        return product

    async def _resolve_category(self, category_id: str) -> dict:
        category = await self.categories.find_by_id(category_id)
        if not category:
            raise ValidationError("Category does not exist")
        return category

    @staticmethod
    def _normalize_sale_price(doc: dict, current_price: float | None = None) -> None:
        """A "was" price only means something when it is above what we charge.
        Anything else is stored as 0 so `on_sale` never lies to the shopper."""
        if "compare_at_price" not in doc:
            return
        price = doc.get("price", current_price)
        if price is None or doc["compare_at_price"] <= price:
            doc["compare_at_price"] = 0.0

    @staticmethod
    def _apply_kind_defaults(doc: dict, product_kind: str) -> None:
        """Keeps the two catalogue lines from carrying each other's fields: a plant
        has care details and no pet types, a pet product the other way round."""
        if product_kind == "plant":
            doc["plant"] = doc.get("plant") or PlantDetails().model_dump()
            doc["suitable_pet_types"] = []
        else:
            doc["plant"] = None

    async def create(self, payload: ProductCreate) -> dict:
        existing = await self.repo.find_by_slug(payload.slug)
        if existing:
            raise ConflictError("Slug already exists")
        category = await self._resolve_category(payload.category_id)

        doc = payload.model_dump()
        doc["category_name"] = category["name"]
        doc["rating"] = 0.0
        doc["rating_count"] = 0
        self._normalize_sale_price(doc)
        self._apply_kind_defaults(doc, payload.product_kind)
        return await self.repo.insert(doc)

    async def update(self, product_id: str, payload: ProductUpdate) -> dict:
        update = {k: v for k, v in payload.model_dump().items() if v is not None}
        if "category_id" in update:
            category = await self._resolve_category(update["category_id"])
            update["category_name"] = category["name"]
        if "compare_at_price" in update:
            # A sale can be set without re-sending the price, so compare against
            # whatever the product currently sells for.
            existing = await self.repo.find_by_id(product_id)
            self._normalize_sale_price(update, existing["price"] if existing else None)
        # Only rewrite the kind-specific fields when the kind itself is being set,
        # so a plain price or stock edit leaves the care details untouched.
        if payload.product_kind is not None:
            self._apply_kind_defaults(update, payload.product_kind)

        product = await self.repo.update_by_id(product_id, update)
        if not product:
            raise NotFoundError("Product not found")

        # A stock edit is the usual way a shelf refills. `flush` decides for
        # itself whether anything is actually back, so this doesn't need to
        # compare before and after.
        if "stock" in update and self.alerts is not None:
            await self.alerts.flush(product_id)
        return product

    async def delete(self, product_id: str) -> None:
        deleted = await self.repo.delete_by_id(product_id)
        if not deleted:
            raise NotFoundError("Product not found")
