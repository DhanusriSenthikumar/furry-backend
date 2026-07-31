from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository


class CategoryRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.categories)

    async def find_by_slug(self, slug: str) -> dict | None:
        return await self.find_one({"slug": slug})
