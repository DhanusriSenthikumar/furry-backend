from fastapi import APIRouter, Depends

from app.deps import get_current_user, get_db
from app.modules.cart.repository import CartRepository
from app.modules.cart.schemas import AddCartItem, CartItemOut, CartOut, UpdateCartItem
from app.modules.cart.service import CartService
from app.modules.products.repository import ProductRepository
from app.modules.products.router import product_out

router = APIRouter(prefix="/cart", tags=["cart"])


def _service(db=Depends(get_db)) -> CartService:
    return CartService(CartRepository(db), ProductRepository(db))


def cart_out(items: list[dict]) -> CartOut:
    item_models = [CartItemOut(product=product_out(i["product"]), quantity=i["quantity"]) for i in items]
    subtotal = sum(i.product.price * i.quantity for i in item_models)
    return CartOut(items=item_models, subtotal=round(subtotal, 2))


@router.get("", response_model=CartOut)
async def get_cart(user: dict = Depends(get_current_user), service: CartService = Depends(_service)):
    items = await service.get_cart(str(user["_id"]))
    return cart_out(items)


@router.post("/items", response_model=CartOut, status_code=201)
async def add_item(
    payload: AddCartItem, user: dict = Depends(get_current_user), service: CartService = Depends(_service)
):
    items = await service.add_item(str(user["_id"]), payload.product_id, payload.quantity)
    return cart_out(items)


@router.patch("/items/{product_id}", response_model=CartOut)
async def update_item(
    product_id: str,
    payload: UpdateCartItem,
    user: dict = Depends(get_current_user),
    service: CartService = Depends(_service),
):
    items = await service.update_item(str(user["_id"]), product_id, payload.quantity)
    return cart_out(items)


@router.delete("/items/{product_id}", response_model=CartOut)
async def remove_item(
    product_id: str, user: dict = Depends(get_current_user), service: CartService = Depends(_service)
):
    items = await service.remove_item(str(user["_id"]), product_id)
    return cart_out(items)


@router.delete("", status_code=204)
async def clear_cart(user: dict = Depends(get_current_user), service: CartService = Depends(_service)):
    await service.clear(str(user["_id"]))
