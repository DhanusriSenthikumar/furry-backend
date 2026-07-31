from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository


class PetRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.pets)

    async def find_by_owner(self, owner_id: str) -> list[dict]:
        return await self.find_many({"owner_id": owner_id}, limit=200, sort=[("_id", -1)])
