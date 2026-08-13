from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository, to_object_id


class NotificationRepository(BaseRepository):
    """One row per thing a customer should know about, newest first."""

    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.notifications)

    async def push(self, doc: dict, dedupe_key: str | None) -> dict | None:
        """Insert a notification, or do nothing if `dedupe_key` was already used.

        Lifecycle hooks are called from paths that can legitimately run twice —
        a status re-saved, a webhook redelivered — and telling someone their
        parcel shipped twice reads as a bug. Keying the write lets the caller
        make an event idempotent without tracking what it has already sent.
        Returns None when the notification was a duplicate.
        """
        if dedupe_key is None:
            return await self.insert(doc)

        result = await self.collection.update_one(
            {"user_id": doc["user_id"], "dedupe_key": dedupe_key},
            {"$setOnInsert": {**doc, "dedupe_key": dedupe_key}},
            upsert=True,
        )
        if result.upserted_id is None:
            return None
        return {**doc, "_id": result.upserted_id, "dedupe_key": dedupe_key}

    async def find_for_user(self, user_id: str, unread_only: bool = False, limit: int = 50) -> list[dict]:
        filter_: dict = {"user_id": user_id}
        if unread_only:
            filter_["read_at"] = None
        return await self.find_many(filter_, limit=limit, sort=[("_id", -1)])

    async def count_unread(self, user_id: str) -> int:
        return await self.count({"user_id": user_id, "read_at": None})

    async def mark_read(self, notification_id: str, user_id: str, now: datetime) -> bool:
        """Scoped by user as well as id so one customer can't touch another's
        feed by guessing an id."""
        try:
            oid = to_object_id(notification_id)
        except ValueError:
            return False
        result = await self.collection.update_one(
            {"_id": oid, "user_id": user_id, "read_at": None},
            {"$set": {"read_at": now}},
        )
        return result.matched_count > 0

    async def mark_all_read(self, user_id: str, now: datetime) -> int:
        result = await self.collection.update_many(
            {"user_id": user_id, "read_at": None},
            {"$set": {"read_at": now}},
        )
        return result.modified_count

    async def delete_for_user(self, notification_id: str, user_id: str) -> bool:
        try:
            oid = to_object_id(notification_id)
        except ValueError:
            return False
        result = await self.collection.delete_one({"_id": oid, "user_id": user_id})
        return result.deleted_count > 0

    async def clear_read(self, user_id: str) -> int:
        result = await self.collection.delete_many({"user_id": user_id, "read_at": {"$ne": None}})
        return result.deleted_count
