from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorCollection


def to_object_id(id_: str) -> ObjectId:
    try:
        return ObjectId(id_)
    except (InvalidId, TypeError):
        raise ValueError(f"Invalid id: {id_}")


class BaseRepository:
    def __init__(self, collection: AsyncIOMotorCollection):
        self.collection = collection

    async def find_by_id(self, id_: str) -> dict | None:
        try:
            oid = to_object_id(id_)
        except ValueError:
            return None
        return await self.collection.find_one({"_id": oid})

    async def find_one(self, filter_: dict) -> dict | None:
        return await self.collection.find_one(filter_)

    async def find_many(
        self,
        filter_: dict | None = None,
        skip: int = 0,
        limit: int = 50,
        sort: list[tuple[str, int]] | None = None,
    ) -> list[dict]:
        cursor = self.collection.find(filter_ or {})
        if sort:
            cursor = cursor.sort(sort)
        cursor = cursor.skip(skip).limit(limit)
        return [doc async for doc in cursor]

    async def count(self, filter_: dict | None = None) -> int:
        return await self.collection.count_documents(filter_ or {})

    async def insert(self, doc: dict) -> dict:
        result = await self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def update_by_id(self, id_: str, update: dict[str, Any]) -> dict | None:
        try:
            oid = to_object_id(id_)
        except ValueError:
            return None
        if not update:
            return await self.find_by_id(id_)
        await self.collection.update_one({"_id": oid}, {"$set": update})
        return await self.collection.find_one({"_id": oid})

    async def delete_by_id(self, id_: str) -> bool:
        try:
            oid = to_object_id(id_)
        except ValueError:
            return False
        result = await self.collection.delete_one({"_id": oid})
        return result.deleted_count > 0
