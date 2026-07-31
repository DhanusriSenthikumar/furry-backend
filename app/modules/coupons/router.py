from fastapi import APIRouter, Depends

from app.core.pagination import Pagination
from app.core.pricing import price_order
from app.deps import get_current_admin, get_current_user, get_db, pagination_params
from app.modules.coupons.repository import CouponRepository
from app.modules.coupons.schemas import (
    CouponApply,
    CouponCreate,
    CouponOut,
    CouponPreviewOut,
    CouponUpdate,
)
from app.modules.coupons.service import CouponService
from app.modules.orders.repository import OrderRepository

router = APIRouter(prefix="/coupons", tags=["coupons"])


def _service(db=Depends(get_db)) -> CouponService:
    return CouponService(CouponRepository(db), OrderRepository(db))


def coupon_out(doc: dict) -> CouponOut:
    return CouponOut(
        id=str(doc["_id"]),
        code=doc["code"],
        description=doc.get("description", ""),
        discount_type=doc["discount_type"],
        value=doc["value"],
        min_subtotal=doc.get("min_subtotal", 0.0),
        max_discount=doc.get("max_discount", 0.0),
        usage_limit=doc.get("usage_limit", 0),
        per_user_limit=doc.get("per_user_limit", 0),
        starts_at=doc.get("starts_at"),
        expires_at=doc.get("expires_at"),
        is_active=doc.get("is_active", True),
        used_count=doc.get("used_count", 0),
    )


@router.post("/apply", response_model=CouponPreviewOut)
async def apply_coupon(
    payload: CouponApply,
    user: dict = Depends(get_current_user),
    service: CouponService = Depends(_service),
):
    """Preview what a code is worth against the current cart, before checkout."""
    coupon, discount = await service.validate(payload.code, payload.subtotal, str(user["_id"]))
    totals = price_order(payload.subtotal, discount)
    return CouponPreviewOut(
        code=coupon["code"],
        description=coupon.get("description", ""),
        discount=totals.discount,
        subtotal=totals.subtotal,
        shipping_fee=totals.shipping_fee,
        tax=totals.tax,
        total=totals.total,
    )


@router.get("", response_model=list[CouponOut])
async def list_coupons(
    pagination: Pagination = Depends(pagination_params),
    _admin: dict = Depends(get_current_admin),
    service: CouponService = Depends(_service),
):
    coupons = await service.list_all(pagination)
    return [coupon_out(c) for c in coupons]


@router.post("", response_model=CouponOut, status_code=201)
async def create_coupon(
    payload: CouponCreate, _admin: dict = Depends(get_current_admin), service: CouponService = Depends(_service)
):
    return coupon_out(await service.create(payload))


@router.put("/{coupon_id}", response_model=CouponOut)
async def update_coupon(
    coupon_id: str,
    payload: CouponUpdate,
    _admin: dict = Depends(get_current_admin),
    service: CouponService = Depends(_service),
):
    return coupon_out(await service.update(coupon_id, payload))


@router.delete("/{coupon_id}", status_code=204)
async def delete_coupon(
    coupon_id: str, _admin: dict = Depends(get_current_admin), service: CouponService = Depends(_service)
):
    await service.delete(coupon_id)
