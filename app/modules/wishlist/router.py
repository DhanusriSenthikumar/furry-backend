from fastapi import APIRouter, Depends

from app.deps import get_current_user, get_db
from app.modules.products.repository import ProductRepository
from app.modules.products.router import product_out
from app.modules.wishlist.repository import WishlistRepository
from app.modules.wishlist.schemas import AddWishlistItem, WishlistOut
from app.modules.wishlist.service import WishlistService

router = APIRouter(prefix="/wishlist", tags=["wishlist"])


def _service(db=Depends(get_db)) -> WishlistService:
    return WishlistService(WishlistRepository(db), ProductRepository(db))


def wishlist_out(products: list[dict]) -> WishlistOut:
    return WishlistOut(items=[product_out(p) for p in products])


@router.get("", response_model=WishlistOut)
async def get_wishlist(user: dict = Depends(get_current_user), service: WishlistService = Depends(_service)):
    products = await service.get(str(user["_id"]))
    return wishlist_out(products)


@router.post("/items", response_model=WishlistOut, status_code=201)
async def add_item(
    payload: AddWishlistItem, user: dict = Depends(get_current_user), service: WishlistService = Depends(_service)
):
    products = await service.add_item(str(user["_id"]), payload.product_id)
    return wishlist_out(products)


@router.delete("/items/{product_id}", response_model=WishlistOut)
async def remove_item(
    product_id: str, user: dict = Depends(get_current_user), service: WishlistService = Depends(_service)
):
    products = await service.remove_item(str(user["_id"]), product_id)
    return wishlist_out(products)
