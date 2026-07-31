from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository


class WishlistRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.wishlists)

    async def find_by_user(self, user_id: str) -> dict | None:
        return await self.find_one({"user_id": user_id})

    async def save_product_ids(self, user_id: str, product_ids: list[str]) -> dict:
        await self.collection.update_one(
            {"user_id": user_id}, {"$set": {"user_id": user_id, "product_ids": product_ids}}, upsert=True
        )
        return await self.find_by_user(user_id)
