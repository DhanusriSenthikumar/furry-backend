from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.base_repository import BaseRepository, to_object_id


class UserRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.users)

    async def find_by_email(self, email: str) -> dict | None:
        return await self.find_one({"email": email})

    async def find_by_reset_token(self, token_hash: str) -> dict | None:
        return await self.find_one({"reset_token_hash": token_hash})

    async def set_reset_token(self, user_id: str, token_hash: str, expires_at: datetime) -> None:
        await self.collection.update_one(
            {"_id": to_object_id(user_id)},
            {"$set": {"reset_token_hash": token_hash, "reset_token_expires_at": expires_at}},
        )

    async def set_password(self, user_id: str, hashed_password: str) -> None:
        """Sets the new password and burns the reset token in one write."""
        await self.collection.update_one(
            {"_id": to_object_id(user_id)},
            {
                "$set": {"hashed_password": hashed_password},
                "$unset": {"reset_token_hash": "", "reset_token_expires_at": ""},
            },
        )
