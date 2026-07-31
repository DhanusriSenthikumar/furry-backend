"""Identity for MCP callers.

The REST API authenticates browsers with an httpOnly `access_token` cookie. MCP
clients aren't browsers, so they send the same JWT as `Authorization: Bearer
<token>` — both are accepted here, and both resolve through the same user
lookup, so an MCP session can never see more than the same person's browser can.
"""

from collections.abc import Mapping
from http.cookies import SimpleCookie

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.security import decode_access_token
from app.db.mongodb import mongodb
from app.modules.users.repository import UserRepository


class McpError(Exception):
    """Surfaced to the MCP client as a tool error with this message."""


def get_database() -> AsyncIOMotorDatabase:
    if mongodb.database is None:
        raise McpError("The store database is not configured — set MONGODB_URI on the API server.")
    return mongodb.database


def _bearer_token(headers: Mapping[str, str]) -> str | None:
    lowered = {key.lower(): value for key, value in headers.items()}

    authorization = lowered.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()

    # A browser-based client (or a proxy replaying the site's session) may send
    # the cookie the storefront already uses.
    cookie_header = lowered.get("cookie")
    if cookie_header:
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except Exception:
            return None
        morsel = cookie.get("access_token")
        if morsel and morsel.value:
            return morsel.value

    return None


async def current_user(headers: Mapping[str, str] | None) -> dict | None:
    """The signed-in customer behind this call, or None for an anonymous client."""
    if not headers:
        return None

    token = _bearer_token(headers)
    if not token:
        return None

    user_id = decode_access_token(token)
    if not user_id:
        return None

    user = await UserRepository(get_database()).find_by_id(user_id)
    if not user or not user.get("is_active", True):
        return None
    return user


async def require_user(headers: Mapping[str, str] | None) -> dict:
    user = await current_user(headers)
    if not user:
        raise McpError(
            "This tool needs a signed-in customer. Send the store's JWT as an "
            "Authorization: Bearer <token> header — POST /auth/token exchanges "
            "an email and password for one."
        )
    return user


async def require_admin(headers: Mapping[str, str] | None) -> dict:
    user = await require_user(headers)
    if not user.get("is_admin"):
        raise McpError("This tool is restricted to store administrators.")
    return user
