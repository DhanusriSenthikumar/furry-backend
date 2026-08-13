"""Subscribe & Save — repeat delivery of the things that run out.

Pet food, litter, and flea treatment get bought on a rhythm, and a store that
makes the customer re-place the same order every month loses it to whichever
store doesn't. A subscription is a standing instruction: this product, this
often, at a standing discount.

Deliveries are placed by `run_due`, which is safe to call from anywhere and as
often as anyone likes — a cron, the admin button, or both at once. It claims
each due row before acting on it, so an order is placed once no matter how many
callers are racing.

The store has no card vault, so a subscription order lands in `pending_payment`
like any other and the customer is told it is waiting. That is a deliberate
limitation rather than a silent one: nothing is charged behind their back.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.pagination import Pagination
from app.modules.notifications.service import NotificationService
from app.modules.orders.service import OrderService
from app.modules.products.repository import ProductRepository
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.schemas import (
    INTERVAL_PRESETS,
    SubscriptionCreate,
    SubscriptionUpdate,
)
from app.modules.users.repository import UserRepository

# A run that dies mid-flight leaves rows claimed. Anything held longer than this
# is assumed orphaned and freed on the next run.
STALE_CLAIM_MINUTES = 30

# How long to wait before retrying a delivery that couldn't be placed — usually
# because the shelf was empty. Short enough to catch a restock, long enough not
# to hammer it.
RETRY_DAYS = 1


class SubscriptionService:
    def __init__(
        self,
        repo: SubscriptionRepository,
        products: ProductRepository,
        users: UserRepository,
        orders: OrderService | None = None,
        notifications: NotificationService | None = None,
    ):
        self.repo = repo
        self.products = products
        self.users = users
        # Only the runner needs it; the management endpoints don't place orders.
        self.orders = orders
        self.notifications = notifications

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    async def _product(self, product_id: str) -> dict:
        product = await self.products.find_by_id(product_id)
        if not product:
            raise NotFoundError("Product not found")
        return product

    async def _decorate(self, doc: dict) -> dict:
        """Attach today's catalogue price to a stored subscription.

        Price is read live rather than frozen at signup: a plan running for a
        year should follow the shelf, in both directions, and showing a stale
        number would make the next delivery a surprise.
        """
        product = await self.products.find_by_id(doc["product_id"])
        unit_price = float(product["price"]) if product else 0.0
        quantity = int(doc.get("quantity", 1))
        discount_percent = float(doc.get("discount_percent", 0.0))

        gross = round(unit_price * quantity, 2)
        estimated = round(gross - gross * discount_percent / 100, 2)

        return {
            **doc,
            "product_name": product["name"] if product else doc.get("product_name", "Unavailable"),
            "product_slug": product.get("slug", "") if product else doc.get("product_slug", ""),
            "product_image": (product.get("images") or [""])[0] if product else "",
            "unit_price": unit_price,
            "estimated_total": estimated,
            "in_stock": bool(product and product.get("stock", 0) >= quantity),
        }

    async def list_mine(self, user_id: str, status: str | None = None) -> list[dict]:
        return [await self._decorate(doc) for doc in await self.repo.find_by_user(user_id, status)]

    async def get_owned(self, subscription_id: str, user: dict) -> dict:
        doc = await self.repo.find_by_id(subscription_id)
        if not doc:
            raise NotFoundError("Subscription not found")
        if doc["user_id"] != str(user["_id"]) and not user.get("is_admin"):
            raise ForbiddenError("Not allowed to view this subscription")
        return doc

    async def offer(self, product_id: str, user_id: str | None) -> dict:
        """What the product page draws. Answered signed-out so the saving is
        visible before anyone commits to an account."""
        product = await self._product(product_id)
        price = float(product["price"])
        discount_percent = settings.subscription_discount_percent
        subscription_price = round(price - price * discount_percent / 100, 2)

        existing = (
            await self.repo.find_active_for_product(user_id, product_id) if user_id else None
        )

        return {
            "enabled": settings.subscriptions_enabled,
            "product_id": str(product["_id"]),
            "discount_percent": discount_percent,
            "unit_price": price,
            "subscription_price": subscription_price,
            "saving_per_delivery": round(price - subscription_price, 2),
            "min_interval_days": settings.subscription_min_interval_days,
            "max_interval_days": settings.subscription_max_interval_days,
            "intervals": INTERVAL_PRESETS,
            "subscribed": existing is not None,
            "subscription_id": str(existing["_id"]) if existing else None,
        }

    # ------------------------------------------------------------------ #
    # Managing
    # ------------------------------------------------------------------ #

    async def create(self, user: dict, payload: SubscriptionCreate) -> dict:
        if not settings.subscriptions_enabled:
            raise ValidationError("Subscriptions are not available right now")

        product = await self._product(payload.product_id)
        user_id = str(user["_id"])

        # One plan per product per customer. Two standing instructions for the
        # same thing is nearly always a mis-click, and the fix — change the
        # quantity on the one you have — is better than a second delivery.
        if await self.repo.find_active_for_product(user_id, payload.product_id):
            raise ConflictError("You already have a subscription for this product")

        now = datetime.now(timezone.utc)
        first_delivery = now if payload.start_now else now + timedelta(days=payload.interval_days)

        doc = await self.repo.insert(
            {
                "user_id": user_id,
                "product_id": str(product["_id"]),
                "product_name": product["name"],
                "product_slug": product.get("slug", ""),
                "quantity": payload.quantity,
                "interval_days": payload.interval_days,
                "discount_percent": settings.subscription_discount_percent,
                "status": "active",
                "next_delivery_at": first_delivery,
                "shipping_address": payload.shipping_address.model_dump(),
                "orders_placed": 0,
                "last_order_id": None,
                "last_ordered_at": None,
                "failure_count": 0,
                "last_error": "",
                "run_batch": None,
                "run_started_at": None,
                "created_at": now,
                "updated_at": now,
            }
        )

        if self.notifications is not None:
            await self.notifications.push(
                user_id,
                "subscription",
                f"Subscription started for {product['name']}",
                f"Every {payload.interval_days} days at {settings.subscription_discount_percent:g}% off. "
                "You can pause or cancel any time.",
                "/subscriptions",
            )
        return await self._decorate(doc)

    async def update(self, subscription_id: str, user: dict, payload: SubscriptionUpdate) -> dict:
        doc = await self.get_owned(subscription_id, user)
        if doc["status"] == "cancelled":
            raise ValidationError("This subscription has been cancelled")

        update: dict = {k: v for k, v in payload.model_dump(exclude={"shipping_address"}).items() if v is not None}
        if payload.shipping_address is not None:
            update["shipping_address"] = payload.shipping_address.model_dump()
        if not update:
            return await self._decorate(doc)

        # Changing the cadence re-bases the next delivery on the last one, so a
        # customer moving from monthly to fortnightly doesn't wait another month
        # for the change to take effect.
        if "interval_days" in update:
            anchor = doc.get("last_ordered_at") or doc.get("created_at") or datetime.now(timezone.utc)
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
            rescheduled = anchor + timedelta(days=update["interval_days"])
            # Never pull a delivery into the past — that would fire it instantly.
            update["next_delivery_at"] = max(rescheduled, datetime.now(timezone.utc))

        update["updated_at"] = datetime.now(timezone.utc)
        return await self._decorate(await self.repo.update_by_id(subscription_id, update))

    async def pause(self, subscription_id: str, user: dict) -> dict:
        doc = await self.get_owned(subscription_id, user)
        if doc["status"] == "cancelled":
            raise ValidationError("This subscription has been cancelled")
        if doc["status"] == "paused":
            return await self._decorate(doc)

        now = datetime.now(timezone.utc)
        return await self._decorate(
            await self.repo.update_by_id(
                subscription_id, {"status": "paused", "paused_at": now, "updated_at": now}
            )
        )

    async def resume(self, subscription_id: str, user: dict) -> dict:
        doc = await self.get_owned(subscription_id, user)
        if doc["status"] == "cancelled":
            raise ValidationError("A cancelled subscription can't be resumed — start a new one")
        if doc["status"] == "active":
            return await self._decorate(doc)

        now = datetime.now(timezone.utc)
        next_at = doc.get("next_delivery_at")
        if next_at is not None and next_at.tzinfo is None:
            next_at = next_at.replace(tzinfo=timezone.utc)
        # A subscription paused past its due date would otherwise fire the
        # moment it resumed, which reads as a bug rather than a delivery.
        if next_at is None or next_at <= now:
            next_at = now + timedelta(days=int(doc.get("interval_days", 30)))

        return await self._decorate(
            await self.repo.update_by_id(
                subscription_id,
                {"status": "active", "next_delivery_at": next_at, "paused_at": None, "updated_at": now},
            )
        )

    async def skip(self, subscription_id: str, user: dict) -> dict:
        """Push the next delivery out by one interval without stopping the plan —
        for the month you're still working through the last bag."""
        doc = await self.get_owned(subscription_id, user)
        if doc["status"] != "active":
            raise ValidationError("Only an active subscription has a delivery to skip")

        now = datetime.now(timezone.utc)
        base = doc.get("next_delivery_at") or now
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        next_at = max(base, now) + timedelta(days=int(doc.get("interval_days", 30)))

        return await self._decorate(
            await self.repo.update_by_id(
                subscription_id, {"next_delivery_at": next_at, "updated_at": now}
            )
        )

    async def cancel(self, subscription_id: str, user: dict) -> dict:
        doc = await self.get_owned(subscription_id, user)
        if doc["status"] == "cancelled":
            return await self._decorate(doc)

        now = datetime.now(timezone.utc)
        return await self._decorate(
            await self.repo.update_by_id(
                subscription_id,
                {"status": "cancelled", "cancelled_at": now, "next_delivery_at": None, "updated_at": now},
            )
        )

    # ------------------------------------------------------------------ #
    # The runner
    # ------------------------------------------------------------------ #

    async def run_due(self, limit: int = 100) -> dict:
        """Place an order for every subscription that has fallen due.

        Idempotent by claim: each row is stamped with this run's batch id before
        anything is ordered, so a second caller finds nothing left to take. Every
        claimed row is released in a `finally`, because a row left claimed would
        never be picked up again.
        """
        now = datetime.now(timezone.utc)
        released = await self.repo.release_stale(now - timedelta(minutes=STALE_CLAIM_MINUTES))

        if not settings.subscriptions_enabled or self.orders is None:
            return {
                "claimed": 0, "ordered": 0, "skipped": 0, "failed": 0, "paused": 0,
                "released_stale": released,
                "details": ["Subscriptions are disabled"] if not settings.subscriptions_enabled else [],
            }

        batch = uuid.uuid4().hex
        due = await self.repo.claim_due(now, batch, limit)

        result = {
            "claimed": len(due), "ordered": 0, "skipped": 0, "failed": 0, "paused": 0,
            "released_stale": released,
            "details": [],
        }

        for subscription in due:
            try:
                outcome, detail = await self._deliver_one(subscription)
            except Exception as exc:
                # Never let one bad row abandon the rest of the batch.
                outcome, detail = "failed", f"unexpected error: {exc}"
                await self.repo.release(str(subscription["_id"]), {"last_error": str(exc)[:200]})
            result[outcome] += 1
            result["details"].append(f"{subscription.get('product_name', 'item')} — {detail}")

        return result

    async def _deliver_one(self, subscription: dict) -> tuple[str, str]:
        """Place one subscription's order. Returns (outcome bucket, detail).

        Always releases the claim, whatever happens.
        """
        subscription_id = str(subscription["_id"])
        try:
            customer = await self.users.find_by_id(subscription["user_id"])
            if not customer or not customer.get("is_active", True):
                await self.repo.release(
                    subscription_id, {"status": "cancelled", "last_error": "Account is no longer active"}
                )
                return "skipped", "account inactive — subscription cancelled"

            product = await self.products.find_by_id(subscription["product_id"])
            if not product:
                await self._fail(subscription, "This product is no longer sold", force_pause=True)
                return "paused", "product withdrawn — subscription paused"

            quantity = int(subscription.get("quantity", 1))
            if product.get("stock", 0) < quantity:
                await self._fail(subscription, f"{product['name']} was out of stock")
                return "failed", "out of stock — will retry"

            order = await self.orders.place_for_subscription(customer, subscription, product)

            now = datetime.now(timezone.utc)
            next_at = now + timedelta(days=int(subscription.get("interval_days", 30)))
            await self.repo.record_order(subscription_id, str(order["_id"]), next_at, now)

            if self.notifications is not None:
                await self.notifications.push(
                    subscription["user_id"],
                    "subscription",
                    f"Your {product['name']} delivery is ready",
                    f"Order #{str(order['_id'])[-8:]} is waiting for payment — "
                    f"${order['total']:.2f} with your subscriber discount.",
                    f"/orders/{order['_id']}",
                    dedupe_key=f"subscription:order:{order['_id']}",
                )
            return "ordered", f"order #{str(order['_id'])[-8:]} placed"
        finally:
            # A no-op once record_order or _fail has already cleared the claim;
            # the safety net for every path that didn't get that far.
            current = await self.repo.find_by_id(subscription_id)
            if current is not None and current.get("run_batch") is not None:
                await self.repo.release(subscription_id)

    async def _fail(self, subscription: dict, reason: str, force_pause: bool = False) -> None:
        """Record a missed delivery, and stop trying after enough of them.

        A subscription that quietly retried forever would be indistinguishable
        from one that was working, so after the configured number of misses it
        pauses and says so.
        """
        subscription_id = str(subscription["_id"])
        failures = int(subscription.get("failure_count", 0)) + 1
        now = datetime.now(timezone.utc)
        give_up = force_pause or failures >= settings.subscription_max_failures

        update = {
            "failure_count": failures,
            "last_error": reason,
            "updated_at": now,
        }
        if give_up:
            update["status"] = "paused"
            update["paused_at"] = now
        else:
            update["next_delivery_at"] = now + timedelta(days=RETRY_DAYS)

        await self.repo.release(subscription_id, update)

        if self.notifications is not None:
            if give_up:
                await self.notifications.push(
                    subscription["user_id"],
                    "subscription",
                    f"Subscription paused — {subscription.get('product_name', 'a product')}",
                    f"{reason}. We've paused it so it stops retrying; resume it whenever you're ready.",
                    "/subscriptions",
                )
            else:
                await self.notifications.push(
                    subscription["user_id"],
                    "subscription",
                    f"Delivery delayed — {subscription.get('product_name', 'a product')}",
                    f"{reason}. We'll try again tomorrow.",
                    "/subscriptions",
                    dedupe_key=f"subscription:retry:{subscription_id}:{now.date().isoformat()}",
                )

    # ------------------------------------------------------------------ #
    # Staff
    # ------------------------------------------------------------------ #

    async def list_all(self, pagination: Pagination, status: str | None = None) -> list[dict]:
        rows = await self.repo.find_all(skip=pagination.skip, limit=pagination.page_size, status=status)
        return [await self._decorate(doc) for doc in rows]

    async def stats(self) -> dict:
        counts = await self.repo.stats()
        return {
            "active": counts.get("active", 0),
            "paused": counts.get("paused", 0),
            "cancelled": counts.get("cancelled", 0),
            "due_now": await self.repo.count_due(datetime.now(timezone.utc)),
        }
