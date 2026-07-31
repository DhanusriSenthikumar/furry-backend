from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository


class OrderRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.orders)

    async def find_by_user(self, user_id: str) -> list[dict]:
        return await self.find_many({"user_id": user_id}, limit=200, sort=[("_id", -1)])

    async def find_all(self, skip: int = 0, limit: int = 20, status: str | None = None) -> list[dict]:
        filter_ = {"status": status} if status else {}
        return await self.find_many(filter_, skip=skip, limit=limit, sort=[("_id", -1)])

    async def has_delivered_product(self, user_id: str, product_id: str) -> bool:
        """Did this customer buy this product and actually receive it?

        Delivered rather than merely paid: a review is a report on the thing in
        your hands. "refunded" still counts — someone who received it and sent it
        back has the most to say about it.
        """
        return (
            await self.count(
                {
                    "user_id": user_id,
                    "status": {"$in": ["delivered", "refunded"]},
                    "items.product_id": product_id,
                }
            )
            > 0
        )

    async def count_coupon_uses(self, code: str, user_id: str) -> int:
        """How many times this user has redeemed a code. Cancelled orders don't count."""
        return await self.count({"user_id": user_id, "coupon_code": code, "status": {"$ne": "cancelled"}})

    async def revenue_and_order_count(self) -> tuple[float, int]:
        pipeline = [
            {"$match": {"status": {"$nin": ["pending_payment", "payment_failed", "cancelled"]}}},
            {"$group": {"_id": None, "revenue": {"$sum": "$total"}, "count": {"$sum": 1}}},
        ]
        result = await self.collection.aggregate(pipeline).to_list(length=1)
        if not result:
            return 0.0, 0
        return round(result[0]["revenue"], 2), result[0]["count"]

    async def count_by_status(self) -> dict[str, int]:
        pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
        result = await self.collection.aggregate(pipeline).to_list(length=None)
        return {row["_id"]: row["count"] for row in result}

    async def revenue_by_day(self, days: int = 30) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        pipeline = [
            {
                "$match": {
                    "created_at": {"$gte": since},
                    "status": {"$nin": ["pending_payment", "payment_failed", "cancelled"]},
                }
            },
            {
                "$group": {
                    "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                    "revenue": {"$sum": "$total"},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        result = await self.collection.aggregate(pipeline).to_list(length=None)
        return [{"date": row["_id"], "revenue": round(row["revenue"], 2)} for row in result]

    async def top_products(self, limit: int = 5) -> list[dict]:
        pipeline = [
            {"$match": {"status": {"$nin": ["pending_payment", "payment_failed", "cancelled"]}}},
            {"$unwind": "$items"},
            {
                "$group": {
                    "_id": {"product_id": "$items.product_id", "name": "$items.name"},
                    "units_sold": {"$sum": "$items.quantity"},
                }
            },
            {"$sort": {"units_sold": -1}},
            {"$limit": limit},
        ]
        result = await self.collection.aggregate(pipeline).to_list(length=limit)
        return [
            {"product_id": row["_id"]["product_id"], "name": row["_id"]["name"], "units_sold": row["units_sold"]}
            for row in result
        ]
