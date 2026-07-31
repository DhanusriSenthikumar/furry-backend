from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository


class QuestionRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.questions)

    async def find_by_product(self, product_id: str, limit: int = 100) -> list[dict]:
        """Answered questions first — they're the ones that help a shopper decide —
        then the newest unanswered ones."""
        return await self.find_many(
            {"product_id": product_id}, limit=limit, sort=[("answer", -1), ("created_at", -1)]
        )

    async def find_unanswered(self, limit: int = 100) -> list[dict]:
        return await self.find_many({"answer": None}, limit=limit, sort=[("created_at", 1)])

    async def count_unanswered(self) -> int:
        return await self.count({"answer": None})
