import asyncio
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.email import email_service
from app.core.exceptions import UnauthorizedError, ValidationError
from app.core.security import (
    create_access_token,
    generate_reset_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.modules.users.repository import UserRepository
from app.modules.users.service import UserService


class AuthService:
    def __init__(self, repo: UserRepository):
        self.repo = repo
        self.users = UserService(repo)

    async def signup(self, name: str, email: str, password: str) -> tuple[dict, str]:
        user = await self.users.create(name, email, password)
        token = create_access_token(str(user["_id"]))
        return user, token

    async def login(self, email: str, password: str) -> tuple[dict, str]:
        user = await self.repo.find_by_email(email)
        if not user or not verify_password(password, user["hashed_password"]):
            raise UnauthorizedError("Invalid email or password")
        if not user.get("is_active", True):
            raise UnauthorizedError("Account is deactivated")
        token = create_access_token(str(user["_id"]))
        return user, token

    async def request_password_reset(self, email: str) -> None:
        """Always succeeds from the caller's point of view — revealing whether an
        address is registered would leak the user list."""
        user = await self.repo.find_by_email(email)
        if not user or not user.get("is_active", True):
            return

        token, token_hash = generate_reset_token()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.reset_token_expire_minutes)
        await self.repo.set_reset_token(str(user["_id"]), token_hash, expires_at)

        try:
            await asyncio.to_thread(email_service.send_password_reset, user["email"], user["name"], token)
        except Exception as exc:
            print(f"Warning: could not send password reset email: {exc}")

    async def reset_password(self, token: str, new_password: str) -> dict:
        user = await self.repo.find_by_reset_token(hash_reset_token(token))
        if not user:
            raise ValidationError("This reset link is invalid or has already been used")

        expires_at = user.get("reset_token_expires_at")
        if expires_at is not None:
            # Mongo returns naive UTC datetimes; normalize before comparing.
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                raise ValidationError("This reset link has expired — request a new one")

        await self.repo.set_password(str(user["_id"]), hash_password(new_password))
        return user
