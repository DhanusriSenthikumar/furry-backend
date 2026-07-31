from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository, to_object_id


class CouponRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.coupons)

    async def find_by_code(self, code: str) -> dict | None:
        return await self.find_one({"code": code.strip().upper()})

    async def increment_usage(self, coupon_id: str) -> None:
        await self.collection.update_one({"_id": to_object_id(coupon_id)}, {"$inc": {"used_count": 1}})
