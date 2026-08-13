from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository, to_object_id


class SubscriptionRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.subscriptions)

    async def find_by_user(self, user_id: str, status: str | None = None) -> list[dict]:
        filter_: dict = {"user_id": user_id}
        if status:
            filter_["status"] = status
        else:
            # A cancelled subscription is history, not a plan — it stays
            # readable by id but doesn't clutter the list.
            filter_["status"] = {"$ne": "cancelled"}
        return await self.find_many(filter_, limit=100, sort=[("next_delivery_at", 1)])

    async def find_active_for_product(self, user_id: str, product_id: str) -> dict | None:
        """Backs the product page's "you already subscribe to this" state."""
        return await self.find_one(
            {"user_id": user_id, "product_id": product_id, "status": {"$ne": "cancelled"}}
        )

    async def claim_due(self, now: datetime, batch: str, limit: int = 100) -> list[dict]:
        """Take ownership of every subscription that has fallen due.

        Stamping the batch id *before* placing any orders is what makes the
        runner safe to call twice — from a cron that overlaps itself, or from an
        admin clicking the button while the cron is mid-flight. Each row is
        claimed by exactly one caller, so an order is placed once.
        """
        due = await self.find_many(
            {"status": "active", "next_delivery_at": {"$lte": now}, "run_batch": None},
            limit=limit,
            sort=[("next_delivery_at", 1)],
        )
        if not due:
            return []

        ids = [doc["_id"] for doc in due]
        await self.collection.update_many(
            {"_id": {"$in": ids}, "run_batch": None},
            {"$set": {"run_batch": batch, "run_started_at": now}},
        )
        return await self.find_many({"run_batch": batch}, limit=limit)

    async def release(self, subscription_id: str, update: dict | None = None) -> dict | None:
        """Finish with a claimed row, applying the outcome and freeing it for the
        next run. Always called, whether the order succeeded or not — a row left
        claimed would never be picked up again."""
        return await self.update_by_id(
            subscription_id, {**(update or {}), "run_batch": None, "run_started_at": None}
        )

    async def release_stale(self, cutoff: datetime) -> int:
        """Free rows whose run died mid-flight — a crashed process, a killed
        container — so a subscription can't be stranded by an outage."""
        result = await self.collection.update_many(
            {"run_batch": {"$ne": None}, "run_started_at": {"$lt": cutoff}},
            {"$set": {"run_batch": None, "run_started_at": None}},
        )
        return result.modified_count

    async def count_due(self, now: datetime) -> int:
        return await self.count({"status": "active", "next_delivery_at": {"$lte": now}})

    async def find_all(self, skip: int = 0, limit: int = 20, status: str | None = None) -> list[dict]:
        filter_ = {"status": status} if status else {}
        return await self.find_many(filter_, skip=skip, limit=limit, sort=[("next_delivery_at", 1)])

    async def stats(self) -> dict:
        cursor = self.collection.aggregate([{"$group": {"_id": "$status", "count": {"$sum": 1}}}])
        return {row["_id"]: row["count"] async for row in cursor}

    async def record_order(self, subscription_id: str, order_id: str, next_at: datetime, now: datetime) -> None:
        await self.collection.update_one(
            {"_id": to_object_id(subscription_id)},
            {
                "$set": {
                    "next_delivery_at": next_at,
                    "last_order_id": order_id,
                    "last_ordered_at": now,
                    "updated_at": now,
                    "failure_count": 0,
                    "last_error": "",
                    "run_batch": None,
                    "run_started_at": None,
                },
                "$inc": {"orders_placed": 1},
            },
        )
