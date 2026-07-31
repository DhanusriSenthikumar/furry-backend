from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository


class ProductViewRepository(BaseRepository):
    """Browsing history: one row per customer per product, stamped with the last
    time they looked at it. Re-viewing moves a product back to the top rather
    than filling the collection with duplicates."""

    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.product_views)

    async def record(self, user_id: str, product_id: str) -> None:
        await self.collection.update_one(
            {"user_id": user_id, "product_id": product_id},
            {"$set": {"viewed_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    async def recent_for_user(self, user_id: str, limit: int = 20) -> list[dict]:
        return await self.find_many({"user_id": user_id}, limit=limit, sort=[("viewed_at", -1)])

    async def clear_for_user(self, user_id: str) -> None:
        await self.collection.delete_many({"user_id": user_id})
