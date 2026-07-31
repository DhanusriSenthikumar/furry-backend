from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository

# A rejected return frees the units back up to be asked for again; every other
# state has a claim on them.
CLAIMING_STATUSES = ["requested", "approved", "refunded"]


class ReturnRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.returns)

    async def find_by_user(self, user_id: str, limit: int = 100) -> list[dict]:
        return await self.find_many({"user_id": user_id}, limit=limit, sort=[("_id", -1)])

    async def find_by_order(self, order_id: str) -> list[dict]:
        return await self.find_many({"order_id": order_id}, limit=100, sort=[("_id", -1)])

    async def find_claiming_by_order(self, order_id: str) -> list[dict]:
        """Returns that still hold units of this order — what a new request has
        to be measured against."""
        return await self.find_many(
            {"order_id": order_id, "status": {"$in": CLAIMING_STATUSES}}, limit=100
        )

    async def find_all(self, skip: int = 0, limit: int = 50, status: str | None = None) -> list[dict]:
        filter_ = {"status": status} if status else {}
        return await self.find_many(filter_, skip=skip, limit=limit, sort=[("_id", -1)])

    async def count_pending(self) -> int:
        return await self.count({"status": "requested"})

    async def total_refunded(self) -> float:
        """Every dollar handed back, for netting off the revenue figure."""
        pipeline = [
            {"$match": {"status": "refunded"}},
            {"$group": {"_id": None, "total": {"$sum": "$refund_amount"}}},
        ]
        result = await self.collection.aggregate(pipeline).to_list(length=1)
        return round(result[0]["total"], 2) if result else 0.0
