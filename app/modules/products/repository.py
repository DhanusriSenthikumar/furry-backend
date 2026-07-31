import re

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository, to_object_id


class ProductRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.products)

    async def find_by_slug(self, slug: str) -> dict | None:
        return await self.find_one({"slug": slug})

    async def find_recommendation_candidates(
        self,
        pet_types: list[str],
        category_ids: list[str],
        brands: list[str],
        limit: int = 120,
    ) -> list[dict]:
        """The pool the recommender ranks: anything in stock that matches the
        customer's pets, the categories they buy from, or the brands they favour.
        With no signals at all it falls back to the best-rated shelf."""
        clauses: list[dict] = []
        if pet_types:
            clauses.append({"suitable_pet_types": {"$in": pet_types}})
        if category_ids:
            clauses.append({"category_id": {"$in": category_ids}})
        if brands:
            clauses.append({"brand": {"$in": brands}})

        filter_: dict = {"stock": {"$gt": 0}, **self.kind_clause("pet")}
        if clauses:
            filter_["$or"] = clauses
        return await self.find_many(filter_, limit=limit, sort=[("rating", -1), ("rating_count", -1)])

    async def find_by_ids(self, product_ids: list[str]) -> list[dict]:
        """Fetches many products at once, preserving the order of `product_ids`."""
        oids = []
        for product_id in product_ids:
            try:
                oids.append(to_object_id(product_id))
            except ValueError:
                continue
        if not oids:
            return []
        docs = {str(doc["_id"]): doc async for doc in self.collection.find({"_id": {"$in": oids}})}
        return [docs[pid] for pid in product_ids if pid in docs]

    async def count_low_stock(self, threshold: int) -> int:
        return await self.count({"stock": {"$lte": threshold}})

    async def find_low_stock(self, threshold: int, limit: int = 50) -> list[dict]:
        """Restock queue — the thinnest shelves first."""
        return await self.find_many({"stock": {"$lte": threshold}}, limit=limit, sort=[("stock", 1)])

    @staticmethod
    def kind_clause(product_kind: str) -> dict:
        """Products predating the plant catalogue have no product_kind field, so
        "pet" means "anything not explicitly a plant" rather than an exact match."""
        return {"product_kind": "plant"} if product_kind == "plant" else {"product_kind": {"$ne": "plant"}}

    def build_filter(
        self,
        category_id: str | None = None,
        pet_type: str | None = None,
        q: str | None = None,
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
    ) -> dict:
        filter_: dict = {}
        if product_kind:
            filter_.update(self.kind_clause(product_kind))
        if category_id:
            filter_["category_id"] = category_id
        if pet_type:
            filter_["suitable_pet_types"] = pet_type
        if plant_type:
            filter_["plant.plant_type"] = plant_type
        if light_needs:
            filter_["plant.light_needs"] = light_needs
        if water_needs:
            filter_["plant.water_needs"] = water_needs
        if care_level:
            filter_["plant.care_level"] = care_level
        if pot_included:
            filter_["plant.pot_included"] = True
        if q:
            # Search names, brands, and descriptions so "salmon" or "Acme" both land.
            escaped = re.escape(q)
            filter_["$or"] = [
                {"name": {"$regex": escaped, "$options": "i"}},
                {"brand": {"$regex": escaped, "$options": "i"}},
                {"description": {"$regex": escaped, "$options": "i"}},
            ]
        if brand:
            filter_["brand"] = brand
        if min_price is not None or max_price is not None:
            price: dict = {}
            if min_price is not None:
                price["$gte"] = min_price
            if max_price is not None:
                price["$lte"] = max_price
            filter_["price"] = price
        if in_stock:
            filter_["stock"] = {"$gt": 0}
        if min_rating is not None:
            filter_["rating"] = {"$gte": min_rating}
        if on_sale:
            # The service stores 0 whenever the "was" price isn't an actual saving,
            # so a positive compare_at_price is enough — no cross-field $expr needed.
            filter_["compare_at_price"] = {"$gt": 0}
        return filter_

    async def distinct_brands(self, product_kind: str | None = None) -> list[str]:
        filter_ = self.kind_clause(product_kind) if product_kind else {}
        brands = await self.collection.distinct("brand", filter_)
        return sorted(b for b in brands if b)

    async def suggest(self, q: str, limit: int = 8, product_kind: str | None = None) -> list[dict]:
        """Lightweight typeahead: name/slug/price/image only, prefix matches first."""
        escaped = re.escape(q)
        kind = self.kind_clause(product_kind) if product_kind else {}
        cursor = (
            self.collection.find(
                {"name": {"$regex": escaped, "$options": "i"}, **kind},
                {"name": 1, "slug": 1, "price": 1, "product_kind": 1, "images": {"$slice": 1}},
            )
            .sort([("rating", -1)])
            .limit(limit)
        )
        return [doc async for doc in cursor]

    async def update_category_name(self, category_id: str, category_name: str) -> None:
        await self.collection.update_many({"category_id": category_id}, {"$set": {"category_name": category_name}})

    async def try_decrement_stock(self, product_id: str, quantity: int) -> bool:
        """Atomically decrements stock only if enough is available. Returns False if not."""
        result = await self.collection.update_one(
            {"_id": to_object_id(product_id), "stock": {"$gte": quantity}},
            {"$inc": {"stock": -quantity}},
        )
        return result.modified_count > 0

    async def restore_stock(self, product_id: str, quantity: int) -> None:
        await self.collection.update_one({"_id": to_object_id(product_id)}, {"$inc": {"stock": quantity}})

    async def set_rating_aggregate(self, product_id: str, rating: float, rating_count: int) -> None:
        await self.collection.update_one(
            {"_id": to_object_id(product_id)}, {"$set": {"rating": rating, "rating_count": rating_count}}
        )
