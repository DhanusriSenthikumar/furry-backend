import secrets

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.db.base_repository import BaseRepository, to_object_id

# No vowels, so a generated code can't spell anything unfortunate, and no 0/O or
# 1/I/L, because these get read off a screen and typed by someone else.
CODE_ALPHABET = "23456789BCDFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


class ReferralRepository(BaseRepository):
    """One row per accepted invite, plus the code lookup on the users collection."""

    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.referrals)
        self.users = db.users

    # ------------------------------------------------------------------ #
    # Codes
    # ------------------------------------------------------------------ #

    async def find_user_by_code(self, code: str) -> dict | None:
        return await self.users.find_one({"referral_code": code.strip().upper()})

    async def claim_code(self, user_id: str) -> str:
        """The customer's code, minting one the first time they ask.

        Generated lazily rather than at signup so the column stays empty for
        accounts that never share, and retried on collision because a random
        code is only unique until it isn't.
        """
        oid = to_object_id(user_id)
        existing = await self.users.find_one({"_id": oid}, {"referral_code": 1})
        if existing and existing.get("referral_code"):
            return existing["referral_code"]

        for _ in range(10):
            code = generate_code()
            try:
                result = await self.users.update_one(
                    {"_id": oid, "referral_code": {"$in": [None, ""]}},
                    {"$set": {"referral_code": code}},
                )
            except DuplicateKeyError:
                continue
            if result.modified_count:
                return code
            # Someone else set it between the read and the write.
            current = await self.users.find_one({"_id": oid}, {"referral_code": 1})
            if current and current.get("referral_code"):
                return current["referral_code"]

        raise RuntimeError("Could not allocate a unique referral code")

    # ------------------------------------------------------------------ #
    # Invites
    # ------------------------------------------------------------------ #

    async def record(self, doc: dict) -> dict | None:
        """Attach a newcomer to their referrer. None if they were already
        attributed — an account can only be referred once, ever."""
        try:
            return await self.insert(doc)
        except DuplicateKeyError:
            return None

    async def find_by_referee(self, referee_id: str) -> dict | None:
        return await self.find_one({"referee_id": referee_id})

    async def find_by_referrer(self, referrer_id: str, limit: int = 100) -> list[dict]:
        return await self.find_many({"referrer_id": referrer_id}, limit=limit, sort=[("_id", -1)])

    async def mark_rewarded(self, referral_id: str, order_id: str, now) -> dict | None:
        """Settle a pending invite. The `status` guard makes the payout
        single-shot even if two deliveries race."""
        result = await self.collection.update_one(
            {"_id": to_object_id(referral_id), "status": "pending"},
            {"$set": {"status": "rewarded", "order_id": order_id, "rewarded_at": now}},
        )
        if result.modified_count == 0:
            return None
        return await self.find_by_id(referral_id)

    async def counts_for(self, referrer_id: str) -> dict[str, int]:
        cursor = self.collection.aggregate(
            [
                {"$match": {"referrer_id": referrer_id}},
                {"$group": {"_id": "$status", "count": {"$sum": 1}}},
            ]
        )
        return {row["_id"]: row["count"] async for row in cursor}

    async def leaderboard(self, limit: int = 20) -> list[dict]:
        """Who is actually bringing people in — rewarded invites only, so an
        account that mass-signed-up friends who never bought doesn't top it."""
        cursor = self.collection.aggregate(
            [
                {"$match": {"status": "rewarded"}},
                {"$group": {"_id": "$referrer_id", "rewarded": {"$sum": 1}}},
                {"$sort": {"rewarded": -1}},
                {"$limit": limit},
            ]
        )
        return [row async for row in cursor]
