from datetime import datetime, timezone

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.modules.orders.repository import OrderRepository
from app.modules.products.repository import ProductRepository
from app.modules.reviews.repository import ReviewRepository
from app.modules.reviews.schemas import ReviewCreate


class ReviewService:
    """Ratings, and the rule that keeps them worth reading.

    A product's `rating` drives the `min_rating` facet and feeds the
    recommendation ranker, so an unverifiable rating isn't just noise on one page
    — it steers what every other shopper is shown. Only a customer who bought the
    product and had it delivered may review it, and the review carries a
    `verified_purchase` flag saying so.
    """

    def __init__(
        self,
        repo: ReviewRepository,
        products: ProductRepository,
        orders: OrderRepository | None = None,
    ):
        self.repo = repo
        self.products = products
        # Without it there is nothing to verify against, so verification is
        # skipped rather than silently blocking every review.
        self.orders = orders

    async def list_for_product(self, product_id: str) -> list[dict]:
        return await self.repo.find_by_product(product_id)

    async def has_purchased(self, user_id: str, product_id: str) -> bool:
        if self.orders is None:
            return False
        return await self.orders.has_delivered_product(user_id, product_id)

    async def eligibility(self, product_id: str, user: dict | None) -> dict:
        """Whether this visitor may review this product, and why not if they
        can't — the form uses it to explain itself rather than failing on submit."""
        if user is None:
            return {
                "can_review": False,
                "reason": "Sign in to review this product",
                "verified_purchase": False,
                "has_reviewed": False,
            }

        user_id = str(user["_id"])
        purchased = await self.has_purchased(user_id, product_id)
        existing = await self.repo.find_by_user_and_product(user_id, product_id)

        if settings.require_verified_purchase and not purchased:
            return {
                "can_review": False,
                "reason": "Only customers who have received this product can review it",
                "verified_purchase": False,
                "has_reviewed": existing is not None,
            }

        return {
            "can_review": True,
            "reason": "",
            "verified_purchase": purchased,
            "has_reviewed": existing is not None,
        }

    async def submit_review(self, product_id: str, user: dict, payload: ReviewCreate) -> dict:
        user_id = str(user["_id"])
        purchased = await self.has_purchased(user_id, product_id)

        if settings.require_verified_purchase and not purchased:
            raise ValidationError(
                "You can review this product once an order containing it has been delivered"
            )

        existing = await self.repo.find_by_user_and_product(user_id, product_id)

        if existing:
            review = await self.repo.update_by_id(
                str(existing["_id"]),
                {
                    "rating": payload.rating,
                    "comment": payload.comment,
                    # Re-checked on every edit, so a review written while
                    # verification was switched off earns its badge later.
                    "verified_purchase": purchased,
                },
            )
        else:
            doc = {
                "product_id": product_id,
                "user_id": user_id,
                "user_name": user["name"],
                "rating": payload.rating,
                "comment": payload.comment,
                "verified_purchase": purchased,
                "created_at": datetime.now(timezone.utc),
            }
            review = await self.repo.insert(doc)

        await self._refresh_product_rating(product_id)
        return review

    async def delete_review(self, review_id: str, user: dict) -> None:
        review = await self.repo.find_by_id(review_id)
        if not review:
            raise NotFoundError("Review not found")
        if review["user_id"] != str(user["_id"]) and not user.get("is_admin"):
            raise ForbiddenError("Not allowed to delete this review")

        await self.repo.delete_by_id(review_id)
        await self._refresh_product_rating(review["product_id"])

    async def _refresh_product_rating(self, product_id: str) -> None:
        rating, count = await self.repo.aggregate_for_product(product_id)
        await self.products.set_rating_aggregate(product_id, rating, count)
