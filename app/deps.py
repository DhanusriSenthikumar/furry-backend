from fastapi import Cookie, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import DatabaseNotConfiguredError, ForbiddenError, UnauthorizedError
from app.core.pagination import Pagination
from app.core.security import decode_access_token
from app.db.mongodb import mongodb
from app.modules.users.repository import UserRepository


def get_db() -> AsyncIOMotorDatabase:
    if mongodb.database is None:
        raise DatabaseNotConfiguredError()
    return mongodb.database


async def get_current_user(access_token: str | None = Cookie(default=None), db=Depends(get_db)) -> dict:
    if not access_token:
        raise UnauthorizedError("Not authenticated")

    user_id = decode_access_token(access_token)
    if not user_id:
        raise UnauthorizedError("Invalid or expired token")

    user = await UserRepository(db).find_by_id(user_id)
    if not user or not user.get("is_active", True):
        raise UnauthorizedError("User not found or deactivated")

    return user


async def get_optional_user(access_token: str | None = Cookie(default=None), db=Depends(get_db)) -> dict | None:
    """The signed-in user, or None. For endpoints a signed-out visitor may call
    but that answer differently once they're known."""
    if not access_token:
        return None

    user_id = decode_access_token(access_token)
    if not user_id:
        return None

    user = await UserRepository(db).find_by_id(user_id)
    return user if user and user.get("is_active", True) else None


async def get_current_admin(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("is_admin"):
        raise ForbiddenError("Admin access required")
    return user


def pagination_params(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Pagination:
    return Pagination(page=page, page_size=page_size)
