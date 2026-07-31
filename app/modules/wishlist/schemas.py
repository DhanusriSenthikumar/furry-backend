from pydantic import BaseModel

from app.modules.products.schemas import ProductOut


class AddWishlistItem(BaseModel):
    product_id: str


class WishlistOut(BaseModel):
    items: list[ProductOut]
