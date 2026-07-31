from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository


class StockAlertRepository(BaseRepository):
    """One row per customer per product. A row is *pending* while `notified_at`
    is None and spent once the email has gone out; re-subscribing after a
    restock resets the same row rather than creating a second one."""

    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.stock_alerts)

    async def find_one_for(self, user_id: str, product_id: str) -> dict | None:
        return await self.find_one({"user_id": user_id, "product_id": product_id})

    async def subscribe(self, user_id: str, product_id: str, now: datetime) -> dict:
        """Idempotent: asking twice leaves one pending row, and asking again
        after a previous alert was sent puts the row back in the queue."""
        await self.collection.update_one(
            {"user_id": user_id, "product_id": product_id},
            {
                "$set": {"notified_at": None, "notify_batch": None},
                "$setOnInsert": {"user_id": user_id, "product_id": product_id, "created_at": now},
            },
            upsert=True,
        )
        return await self.find_one_for(user_id, product_id)

    async def unsubscribe(self, user_id: str, product_id: str) -> bool:
        result = await self.collection.delete_one({"user_id": user_id, "product_id": product_id})
        return result.deleted_count > 0

    async def count_pending(self, product_id: str) -> int:
        return await self.count({"product_id": product_id, "notified_at": None})

    async def claim_pending(self, product_id: str, batch: str, now: datetime) -> list[dict]:
        """Marks every pending row for this product as notified and returns just
        the rows this call claimed.

        Claiming before sending is what makes an alert fire exactly once: two
        restocks landing at the same moment both run this update, but only one
        of them stamps any given row with its own batch id, so only one of them
        gets that row back to email.
        """
        await self.collection.update_many(
            {"product_id": product_id, "notified_at": None},
            {"$set": {"notified_at": now, "notify_batch": batch}},
        )
        return await self.find_many({"notify_batch": batch}, limit=0)

    async def release(self, alert_id: str) -> None:
        """Puts a claimed row back in the queue — used when the email could not
        be sent, so the customer still hears about the next restock."""
        await self.update_by_id(alert_id, {"notified_at": None, "notify_batch": None})

    async def demand(self, limit: int = 50) -> list[dict]:
        """Pending requests grouped by product, most-wanted first."""
        cursor = self.collection.aggregate(
            [
                {"$match": {"notified_at": None}},
                {
                    "$group": {
                        "_id": "$product_id",
                        "waiting": {"$sum": 1},
                        "oldest_request": {"$min": "$created_at"},
                    }
                },
                {"$sort": {"waiting": -1, "oldest_request": 1}},
                {"$limit": limit},
            ]
        )
        return [doc async for doc in cursor]
