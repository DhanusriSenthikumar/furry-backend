"""Storage for the points ledger and the balance it summarises.

Two collections are involved. `loyalty_entries` is the append-only history — one
row per movement, never edited — and the customer's `users` document carries the
running balance so a checkout doesn't have to sum a lifetime of rows to find out
whether it can afford a discount.

Keeping a cached total alongside the log means they can in principle disagree,
so `balance_from_ledger` exists to prove they don't, and the admin recompute
endpoint uses it to repair them if they ever do.
"""

from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.db.base_repository import BaseRepository, to_object_id


class LoyaltyRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.loyalty_entries)
        self.users = db.users

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    async def balances(self, user_id: str) -> tuple[int, int]:
        """(spendable balance, lifetime earned). Lifetime drives the tier and
        only ever goes up, so spending points never costs a customer status."""
        try:
            oid = to_object_id(user_id)
        except ValueError:
            return 0, 0
        doc = await self.users.find_one(
            {"_id": oid}, {"loyalty_points": 1, "loyalty_lifetime_points": 1}
        )
        if not doc:
            return 0, 0
        return int(doc.get("loyalty_points", 0) or 0), int(doc.get("loyalty_lifetime_points", 0) or 0)

    async def history(self, user_id: str, skip: int = 0, limit: int = 50) -> list[dict]:
        return await self.find_many({"user_id": user_id}, skip=skip, limit=limit, sort=[("_id", -1)])

    async def count_for_user(self, user_id: str) -> int:
        return await self.count({"user_id": user_id})

    async def balance_from_ledger(self, user_id: str) -> tuple[int, int]:
        """Recompute both totals from the history. The authority when the cached
        balance is in doubt."""
        cursor = self.collection.aggregate(
            [
                {"$match": {"user_id": user_id}},
                {
                    "$group": {
                        "_id": None,
                        "balance": {"$sum": "$points"},
                        "lifetime": {"$sum": {"$cond": [{"$gt": ["$points", 0]}, "$points", 0]}},
                    }
                },
            ]
        )
        rows = [row async for row in cursor]
        if not rows:
            return 0, 0
        return int(rows[0]["balance"]), int(rows[0]["lifetime"])

    async def has_entry(self, dedupe_key: str) -> bool:
        return await self.find_one({"dedupe_key": dedupe_key}) is not None

    # ------------------------------------------------------------------ #
    # Writing
    #
    # Credits and debits guard different risks, so they order their two writes
    # differently. A credit must not be applied twice, so the ledger row — whose
    # unique index on `dedupe_key` is the only real lock available — goes first
    # and its rejection is the signal to stop. A debit must not overdraw, so the
    # guarded decrement of the balance goes first and *its* rejection is the
    # signal to stop.
    # ------------------------------------------------------------------ #

    async def credit(
        self,
        user_id: str,
        points: int,
        kind: str,
        reason: str,
        now: datetime,
        order_id: str | None = None,
        dedupe_key: str | None = None,
    ) -> dict | None:
        """Add points. Returns None if `dedupe_key` has already been credited."""
        if points <= 0:
            return None

        entry = {
            "user_id": user_id,
            "points": points,
            "kind": kind,
            "reason": reason,
            "order_id": order_id,
            "created_at": now,
        }
        if dedupe_key is not None:
            entry["dedupe_key"] = dedupe_key

        try:
            await self.collection.insert_one(dict(entry))
        except DuplicateKeyError:
            # Already credited by an earlier run of the same event.
            return None

        await self.users.update_one(
            {"_id": to_object_id(user_id)},
            {"$inc": {"loyalty_points": points, "loyalty_lifetime_points": points}},
        )
        return entry

    async def debit(
        self,
        user_id: str,
        points: int,
        kind: str,
        reason: str,
        now: datetime,
        order_id: str | None = None,
    ) -> bool:
        """Spend points, atomically. False when the balance won't cover it.

        The `$gte` guard is what makes two checkouts racing for the same points
        safe: Mongo applies the filter and the decrement as one operation, so
        exactly one of them matches.
        """
        if points <= 0:
            return True

        try:
            oid = to_object_id(user_id)
        except ValueError:
            return False

        result = await self.users.update_one(
            {"_id": oid, "loyalty_points": {"$gte": points}},
            {"$inc": {"loyalty_points": -points}},
        )
        if result.matched_count == 0:
            return False

        await self.collection.insert_one(
            {
                "user_id": user_id,
                "points": -points,
                "kind": kind,
                "reason": reason,
                "order_id": order_id,
                "created_at": now,
            }
        )
        return True

    async def set_balances(self, user_id: str, balance: int, lifetime: int) -> None:
        """Overwrite the cached totals. Only for reconciliation against the ledger."""
        await self.users.update_one(
            {"_id": to_object_id(user_id)},
            {"$set": {"loyalty_points": balance, "loyalty_lifetime_points": lifetime}},
        )

    async def points_spent_on_order(self, order_id: str) -> int:
        """How many points this order consumed, net of anything already given
        back — so a second partial return can't re-refund the first one's points."""
        cursor = self.collection.aggregate(
            [
                {"$match": {"order_id": order_id, "kind": {"$in": ["redeemed", "refunded"]}}},
                {"$group": {"_id": None, "net": {"$sum": "$points"}}},
            ]
        )
        rows = [row async for row in cursor]
        # Redemptions are negative and refunds positive, so the net is negative
        # while any of the spend is still outstanding.
        return -int(rows[0]["net"]) if rows else 0
