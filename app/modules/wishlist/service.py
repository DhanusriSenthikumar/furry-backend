from app.core.exceptions import NotFoundError
from app.modules.products.repository import ProductRepository
from app.modules.wishlist.repository import WishlistRepository


class WishlistService:
    def __init__(self, repo: WishlistRepository, products: ProductRepository):
        self.repo = repo
        self.products = products

    async def _hydrate(self, product_ids: list[str]) -> list[dict]:
        products = []
        for product_id in product_ids:
            product = await self.products.find_by_id(product_id)
            if product:
                products.append(product)
        return products

    async def get(self, user_id: str) -> list[dict]:
        wishlist = await self.repo.find_by_user(user_id)
        product_ids = wishlist["product_ids"] if wishlist else []
        return await self._hydrate(product_ids)

    async def add_item(self, user_id: str, product_id: str) -> list[dict]:
        product = await self.products.find_by_id(product_id)
        if not product:
            raise NotFoundError("Product not found")

        wishlist = await self.repo.find_by_user(user_id)
        product_ids = wishlist["product_ids"] if wishlist else []
        if product_id not in product_ids:
            product_ids.append(product_id)

        updated = await self.repo.save_product_ids(user_id, product_ids)
        return await self._hydrate(updated["product_ids"])

    async def remove_item(self, user_id: str, product_id: str) -> list[dict]:
        wishlist = await self.repo.find_by_user(user_id)
        product_ids = wishlist["product_ids"] if wishlist else []
        product_ids = [p for p in product_ids if p != product_id]

        updated = await self.repo.save_product_ids(user_id, product_ids)
        return await self._hydrate(updated["product_ids"])
