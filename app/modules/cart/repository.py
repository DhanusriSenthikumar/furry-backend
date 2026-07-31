from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository


class CartRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.carts)

    async def find_by_user(self, user_id: str) -> dict | None:
        return await self.find_one({"user_id": user_id})

    async def save_items(self, user_id: str, items: list[dict]) -> dict:
        await self.collection.update_one(
            {"user_id": user_id}, {"$set": {"user_id": user_id, "items": items}}, upsert=True
        )
        return await self.find_by_user(user_id)
