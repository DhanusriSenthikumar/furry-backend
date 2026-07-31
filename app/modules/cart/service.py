from app.core.exceptions import NotFoundError
from app.modules.cart.repository import CartRepository
from app.modules.products.repository import ProductRepository


class CartService:
    def __init__(self, repo: CartRepository, products: ProductRepository):
        self.repo = repo
        self.products = products

    async def _hydrate(self, items: list[dict]) -> list[dict]:
        hydrated = []
        for item in items:
            product = await self.products.find_by_id(item["product_id"])
            if product:
                hydrated.append({"product": product, "quantity": item["quantity"]})
        return hydrated

    async def get_cart(self, user_id: str) -> list[dict]:
        cart = await self.repo.find_by_user(user_id)
        items = cart["items"] if cart else []
        return await self._hydrate(items)

    async def add_item(self, user_id: str, product_id: str, quantity: int) -> list[dict]:
        product = await self.products.find_by_id(product_id)
        if not product:
            raise NotFoundError("Product not found")

        cart = await self.repo.find_by_user(user_id)
        items = cart["items"] if cart else []
        existing = next((i for i in items if i["product_id"] == product_id), None)
        if existing:
            existing["quantity"] += quantity
        else:
            items.append({"product_id": product_id, "quantity": quantity})

        updated = await self.repo.save_items(user_id, items)
        return await self._hydrate(updated["items"])

    async def update_item(self, user_id: str, product_id: str, quantity: int) -> list[dict]:
        cart = await self.repo.find_by_user(user_id)
        items = cart["items"] if cart else []
        if quantity <= 0:
            items = [i for i in items if i["product_id"] != product_id]
        else:
            found = False
            for item in items:
                if item["product_id"] == product_id:
                    item["quantity"] = quantity
                    found = True
            if not found:
                raise NotFoundError("Item not in cart")

        updated = await self.repo.save_items(user_id, items)
        return await self._hydrate(updated["items"])

    async def remove_item(self, user_id: str, product_id: str) -> list[dict]:
        return await self.update_item(user_id, product_id, 0)

    async def clear(self, user_id: str) -> None:
        await self.repo.save_items(user_id, [])
