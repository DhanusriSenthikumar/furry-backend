from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import settings


class MongoDB:
    client: AsyncIOMotorClient | None = None
    database: AsyncIOMotorDatabase | None = None


mongodb = MongoDB()


def connect() -> None:
    if not settings.mongodb_uri:
        mongodb.client = None
        mongodb.database = None
        return
    mongodb.client = AsyncIOMotorClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    mongodb.database = mongodb.client[settings.db_name]


def disconnect() -> None:
    if mongodb.client is not None:
        mongodb.client.close()
    mongodb.client = None
    mongodb.database = None


connect()
