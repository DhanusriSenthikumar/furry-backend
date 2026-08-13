from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository, to_object_id

# Anything past this is closed to new replies. Reopening is a deliberate act, so
# a thread from last year can't be revived by a stray message.
CLOSED_STATUSES = {"closed"}


class SupportRepository(BaseRepository):
    """Tickets, each carrying its whole conversation.

    Messages are embedded rather than kept in their own collection: a thread is
    always read in full, is never long enough to strain a document, and is
    meaningless apart from its ticket.
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.support_tickets)

    async def find_by_user(self, user_id: str, status: str | None = None) -> list[dict]:
        filter_: dict = {"user_id": user_id}
        if status:
            filter_["status"] = status
        return await self.find_many(filter_, limit=100, sort=[("last_message_at", -1)])

    async def find_all(
        self, skip: int = 0, limit: int = 20, status: str | None = None, priority: str | None = None
    ) -> list[dict]:
        filter_: dict = {}
        if status:
            filter_["status"] = status
        if priority:
            filter_["priority"] = priority
        # Oldest waiting first within the queue — the fairest order to work in,
        # and the one that keeps anyone from being forgotten.
        return await self.find_many(filter_, skip=skip, limit=limit, sort=[("last_message_at", 1)])

    async def append_message(self, ticket_id: str, message: dict, update: dict) -> dict | None:
        """Add a reply and move the ticket's state in one write, so a thread can
        never show a message the status hasn't caught up with."""
        oid = to_object_id(ticket_id)
        await self.collection.update_one(
            {"_id": oid}, {"$push": {"messages": message}, "$set": update}
        )
        return await self.collection.find_one({"_id": oid})

    async def count_open(self) -> int:
        return await self.count({"status": {"$in": ["open", "pending"]}})

    async def count_awaiting_staff(self) -> int:
        """Tickets where the customer spoke last — the real size of the queue."""
        return await self.count({"status": {"$nin": list(CLOSED_STATUSES)}, "awaiting": "staff"})

    async def count_unread_for_user(self, user_id: str) -> int:
        return await self.count({"user_id": user_id, "customer_unread": True})

    async def stats(self) -> dict:
        cursor = self.collection.aggregate([{"$group": {"_id": "$status", "count": {"$sum": 1}}}])
        return {row["_id"]: row["count"] async for row in cursor}

    async def oldest_waiting(self) -> datetime | None:
        rows = await self.find_many(
            {"status": {"$nin": list(CLOSED_STATUSES)}, "awaiting": "staff"},
            limit=1,
            sort=[("last_message_at", 1)],
        )
        return rows[0].get("last_message_at") if rows else None
