from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository


class PaymentRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.payments)

    async def find_by_order(self, order_id: str) -> dict | None:
        return await self.find_one({"order_id": order_id})

    async def find_by_reference(self, gateway: str, gateway_reference: str) -> dict | None:
        return await self.find_one({"gateway": gateway, "gateway_reference": gateway_reference})
