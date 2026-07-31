from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository


class ReviewRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.reviews)

    async def find_by_product(self, product_id: str) -> list[dict]:
        return await self.find_many({"product_id": product_id}, limit=200, sort=[("_id", -1)])

    async def find_by_user_and_product(self, user_id: str, product_id: str) -> dict | None:
        return await self.find_one({"user_id": user_id, "product_id": product_id})

    async def aggregate_for_product(self, product_id: str) -> tuple[float, int]:
        pipeline = [
            {"$match": {"product_id": product_id}},
            {"$group": {"_id": None, "avg_rating": {"$avg": "$rating"}, "count": {"$sum": 1}}},
        ]
        result = await self.collection.aggregate(pipeline).to_list(length=1)
        if not result:
            return 0.0, 0
        return round(result[0]["avg_rating"], 2), result[0]["count"]
