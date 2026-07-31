from datetime import datetime, timezone

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.pagination import Pagination
from app.modules.coupons.repository import CouponRepository
from app.modules.coupons.schemas import CouponCreate, CouponUpdate
from app.modules.orders.repository import OrderRepository


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Dates entered as plain YYYY-MM-DD are naive; treat them as UTC so the
    # comparison below never raises on mixed awareness.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class CouponService:
    def __init__(self, repo: CouponRepository, orders: OrderRepository):
        self.repo = repo
        self.orders = orders

    async def list_all(self, pagination: Pagination) -> list[dict]:
        return await self.repo.find_many({}, skip=pagination.skip, limit=pagination.page_size, sort=[("_id", -1)])

    async def create(self, payload: CouponCreate) -> dict:
        if await self.repo.find_by_code(payload.code):
            raise ConflictError("A coupon with that code already exists")
        doc = payload.model_dump()
        doc["used_count"] = 0
        return await self.repo.insert(doc)

    async def update(self, coupon_id: str, payload: CouponUpdate) -> dict:
        update = {k: v for k, v in payload.model_dump().items() if v is not None}
        coupon = await self.repo.update_by_id(coupon_id, update)
        if not coupon:
            raise NotFoundError("Coupon not found")
        return coupon

    async def delete(self, coupon_id: str) -> None:
        if not await self.repo.delete_by_id(coupon_id):
            raise NotFoundError("Coupon not found")

    async def validate(self, code: str, subtotal: float, user_id: str) -> tuple[dict, float]:
        """Returns the coupon and the discount it yields, or raises ValidationError."""
        coupon = await self.repo.find_by_code(code)
        if not coupon:
            raise ValidationError("That coupon code isn't valid")
        if not coupon.get("is_active", True):
            raise ValidationError("That coupon is no longer active")

        now = datetime.now(timezone.utc)
        starts_at = _parse(coupon.get("starts_at"))
        expires_at = _parse(coupon.get("expires_at"))
        if starts_at and now < starts_at:
            raise ValidationError("That coupon isn't active yet")
        if expires_at and now > expires_at:
            raise ValidationError("That coupon has expired")

        min_subtotal = coupon.get("min_subtotal", 0)
        if subtotal < min_subtotal:
            raise ValidationError(f"Spend at least ${min_subtotal:.2f} to use this coupon")

        usage_limit = coupon.get("usage_limit", 0)
        if usage_limit and coupon.get("used_count", 0) >= usage_limit:
            raise ValidationError("That coupon has been fully redeemed")

        per_user_limit = coupon.get("per_user_limit", 0)
        if per_user_limit:
            used_by_user = await self.orders.count_coupon_uses(coupon["code"], user_id)
            if used_by_user >= per_user_limit:
                raise ValidationError("You've already used that coupon")

        return coupon, self.discount_for(coupon, subtotal)

    @staticmethod
    def discount_for(coupon: dict, subtotal: float) -> float:
        if coupon["discount_type"] == "percent":
            discount = subtotal * coupon["value"] / 100
            max_discount = coupon.get("max_discount", 0)
            if max_discount:
                discount = min(discount, max_discount)
        else:
            discount = coupon["value"]
        return round(min(discount, subtotal), 2)

    async def mark_redeemed(self, coupon_id: str) -> None:
        await self.repo.increment_usage(coupon_id)
