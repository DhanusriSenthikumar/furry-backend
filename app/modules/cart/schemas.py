from pydantic import BaseModel, Field

from app.modules.products.schemas import ProductOut


class AddCartItem(BaseModel):
    product_id: str
    quantity: int = Field(gt=0, default=1)


class UpdateCartItem(BaseModel):
    quantity: int = Field(ge=0)


class CartItemOut(BaseModel):
    product: ProductOut
    quantity: int


class CartOut(BaseModel):
    items: list[CartItemOut]
    subtotal: float
