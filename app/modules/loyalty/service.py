"""Loyalty points — earning, spending, and the tier they add up to.

The rules live in `app.core.loyalty`; this module decides when to apply them and
owns the two events that move a balance:

  * an order reaches *delivered* and earns points, once and only once
  * an order is cancelled or refunded, and gives them back

Every write is keyed so that re-running an event is a no-op. That matters more
here than elsewhere in the store, because points are money: a webhook redelivered
by a payment provider, or an admin nudging a status back and forth, must not mint
new balance each time.
"""

from datetime import datetime, timezone

from app.core import loyalty as rules
from app.core.config import settings
from app.core.exceptions import ValidationError
from app.core.pagination import Pagination
from app.modules.loyalty.repository import LoyaltyRepository
from app.modules.notifications.service import NotificationService


class LoyaltyService:
    def __init__(self, repo: LoyaltyRepository, notifications: NotificationService | None = None):
        self.repo = repo
        # Points landing silently is a wasted retention moment. Optional so the
        # ledger still works in contexts that have no feed wired up.
        self.notifications = notifications

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    async def summary(self, user_id: str) -> dict:
        balance, lifetime = await self.repo.balances(user_id)
        return {
            "enabled": settings.loyalty_enabled,
            "balance": balance,
            "balance_value": rules.redemption_value(balance),
            "standing": rules.standing(lifetime).as_dict(),
            "points_per_currency": settings.loyalty_points_per_currency,
            "points_per_redeemed_currency": settings.loyalty_points_per_redeemed_currency,
            "min_redemption": settings.loyalty_min_redemption,
            "max_redemption_percent": settings.loyalty_max_redemption_percent,
            "recent": await self.repo.history(user_id, limit=10),
        }

    async def history(self, user_id: str, pagination: Pagination) -> list[dict]:
        return await self.repo.history(user_id, skip=pagination.skip, limit=pagination.page_size)

    async def preview(self, user_id: str, subtotal: float) -> dict:
        """What this customer could put towards a basket of `subtotal`."""
        balance, _lifetime = await self.repo.balances(user_id)
        max_points = rules.max_redeemable_points(balance, subtotal)

        reason = ""
        if not settings.loyalty_enabled:
            reason = "Rewards are not available right now"
        elif balance < settings.loyalty_min_redemption:
            needed = settings.loyalty_min_redemption - balance
            reason = f"You need {needed:,} more points before you can redeem"
        elif max_points <= 0:
            reason = "This basket is too small to redeem points against"

        return {
            "balance": balance,
            "max_points": max_points,
            "max_value": rules.redemption_value(max_points),
            "min_redemption": settings.loyalty_min_redemption,
            "eligible": max_points > 0,
            "reason": reason,
        }

    # ------------------------------------------------------------------ #
    # Checkout
    # ------------------------------------------------------------------ #

    async def quote_redemption(self, user_id: str, subtotal: float, requested: int | None) -> tuple[int, float]:
        """(points that would be spent, what they are worth) — no balance moves.

        An over-ask is clamped rather than rejected: baskets shrink between the
        quote and the order, and charging a customer more because they removed an
        item would be indefensible.
        """
        if not requested or not settings.loyalty_enabled:
            return 0, 0.0
        balance, _lifetime = await self.repo.balances(user_id)
        points = rules.clamp_redemption(requested, balance, subtotal)
        return points, rules.redemption_value(points)

    async def spend(self, user_id: str, points: int, order_id: str, reason: str = "") -> bool:
        """Take the points for an order. False if the balance no longer covers it."""
        if points <= 0:
            return True
        return await self.repo.debit(
            user_id,
            points,
            "redeemed",
            reason or f"Redeemed against order #{order_id[-8:]}",
            datetime.now(timezone.utc),
            order_id=order_id,
        )

    async def refund_spend(self, user_id: str, points: int, order_id: str, reason: str) -> None:
        """Hand back points that were spent on an order that came undone."""
        if points <= 0:
            return
        await self.repo.credit(
            user_id,
            points,
            "refunded",
            reason,
            datetime.now(timezone.utc),
            order_id=order_id,
        )

    # ------------------------------------------------------------------ #
    # Order lifecycle
    # ------------------------------------------------------------------ #

    @staticmethod
    def _earnable_spend(order: dict) -> float:
        """What an order earns on: goods less every discount, before tax and
        shipping. Points spent on the order don't reduce it — the customer paid
        for those goods once already, with points they had earned."""
        subtotal = float(order.get("subtotal", order.get("total", 0.0)) or 0.0)
        discount = float(order.get("discount", 0.0) or 0.0)
        return max(round(subtotal - discount, 2), 0.0)

    async def award_for_order(self, order: dict) -> int:
        """Credit the points a delivered order earned. Safe to call repeatedly."""
        if not settings.loyalty_enabled:
            return 0

        user_id = order.get("user_id")
        order_id = str(order["_id"])
        if not user_id:
            return 0

        _balance, lifetime = await self.repo.balances(user_id)
        points = rules.points_for_spend(self._earnable_spend(order), lifetime)
        if points <= 0:
            return 0

        entry = await self.repo.credit(
            user_id,
            points,
            "earned",
            f"Order #{order_id[-8:]} delivered",
            datetime.now(timezone.utc),
            order_id=order_id,
            dedupe_key=f"earn:{order_id}",
        )
        if entry is None:
            # Already awarded — the order was re-marked delivered.
            return 0

        if self.notifications is not None:
            await self.notifications.push(
                user_id,
                "reward",
                f"You earned {points:,} points",
                f"Order #{order_id[-8:]} is delivered — that's "
                f"${rules.redemption_value(points):.2f} towards your next order.",
                "/rewards",
                dedupe_key=f"reward:{order_id}",
            )
        return points

    async def reverse_for_order(self, order: dict, portion: float = 1.0, reason: str = "") -> None:
        """Undo an order's points when it is cancelled or refunded.

        Both directions have to move. Points the order *earned* are clawed back,
        or the store pays rewards on a sale that didn't stick. Points the order
        *spent* are returned, or the customer is out both the goods and the
        balance they paid with.

        `portion` is the share of the order coming back, so a partial return
        settles a proportional slice of each.
        """
        if not settings.loyalty_enabled:
            return

        user_id = order.get("user_id")
        order_id = str(order["_id"])
        if not user_id:
            return
        portion = min(max(portion, 0.0), 1.0)
        if portion <= 0:
            return

        label = reason or f"Order #{order_id[-8:]} reversed"

        # Points spent on it come back first — that is the customer's own money.
        outstanding = await self.repo.points_spent_on_order(order_id)
        if outstanding > 0:
            await self.refund_spend(user_id, int(outstanding * portion), order_id, f"{label} — points returned")

        # And anything it earned goes back out, but only down to zero: a
        # customer who has already spent their rewards isn't pushed negative.
        earned = await self.repo.find_one({"order_id": order_id, "kind": "earned"})
        if earned:
            claw_back = int(earned["points"] * portion)
            balance, _lifetime = await self.repo.balances(user_id)
            claw_back = min(claw_back, max(balance, 0))
            if claw_back > 0:
                await self.repo.debit(
                    user_id,
                    claw_back,
                    "reversed",
                    f"{label} — points withdrawn",
                    datetime.now(timezone.utc),
                    order_id=order_id,
                )

    # ------------------------------------------------------------------ #
    # Referral and staff credits
    # ------------------------------------------------------------------ #

    async def credit_bonus(
        self,
        user_id: str,
        points: int,
        kind: str,
        reason: str,
        dedupe_key: str | None = None,
        notify_title: str = "",
    ) -> int:
        """Award points outside the order lifecycle — a referral, or goodwill."""
        if points <= 0 or not settings.loyalty_enabled:
            return 0

        entry = await self.repo.credit(
            user_id, points, kind, reason, datetime.now(timezone.utc), dedupe_key=dedupe_key
        )
        if entry is None:
            return 0

        if self.notifications is not None and notify_title:
            await self.notifications.push(
                user_id,
                "reward" if kind != "referral" else "referral",
                notify_title,
                f"{points:,} points are in your balance — worth "
                f"${rules.redemption_value(points):.2f} off your next order.",
                "/rewards",
                dedupe_key=f"bonus:{dedupe_key}" if dedupe_key else None,
            )
        return points

    async def adjust(self, user_id: str, points: int, reason: str, admin_name: str) -> dict:
        """Staff moving a balance by hand, in either direction."""
        if points == 0:
            raise ValidationError("An adjustment of zero points does nothing")

        note = f"{reason} (by {admin_name})"
        now = datetime.now(timezone.utc)
        if points > 0:
            await self.repo.credit(user_id, points, "adjustment", note, now)
        else:
            balance, _lifetime = await self.repo.balances(user_id)
            if balance < -points:
                raise ValidationError(f"That customer only has {balance:,} points")
            await self.repo.debit(user_id, -points, "adjustment", note, now)

        if self.notifications is not None:
            await self.notifications.push(
                user_id,
                "reward",
                "Your rewards balance was adjusted" if points < 0 else f"You were given {points:,} points",
                reason,
                "/rewards",
            )
        return await self.summary(user_id)

    async def reconcile(self, user_id: str) -> dict:
        """Rebuild the cached balance from the ledger, which is the record of
        record. A no-op when they already agree."""
        balance, lifetime = await self.repo.balance_from_ledger(user_id)
        cached_balance, cached_lifetime = await self.repo.balances(user_id)
        if (balance, lifetime) != (cached_balance, cached_lifetime):
            await self.repo.set_balances(user_id, balance, lifetime)
        return {
            "user_id": user_id,
            "was": {"balance": cached_balance, "lifetime": cached_lifetime},
            "now": {"balance": balance, "lifetime": lifetime},
            "changed": (balance, lifetime) != (cached_balance, cached_lifetime),
        }
