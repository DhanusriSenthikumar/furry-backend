import uuid

from app.core.exceptions import ConflictError, NotFoundError
from app.core.pagination import Pagination
from app.core.security import hash_password
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import AddressCreate, AddressUpdate, AdminUserUpdate, UserUpdate


class UserService:
    def __init__(self, repo: UserRepository):
        self.repo = repo

    async def get_by_id(self, user_id: str) -> dict:
        user = await self.repo.find_by_id(user_id)
        if not user:
            raise NotFoundError("User not found")
        return user

    async def create(self, name: str, email: str, password: str, is_admin: bool = False) -> dict:
        existing = await self.repo.find_by_email(email)
        if existing:
            raise ConflictError("Email already registered")
        doc = {
            "name": name,
            "email": email,
            "hashed_password": hash_password(password),
            "is_admin": is_admin,
            "is_active": True,
            "addresses": [],
        }
        return await self.repo.insert(doc)

    async def update_profile(self, user_id: str, payload: UserUpdate) -> dict:
        update = {k: v for k, v in payload.model_dump().items() if v is not None}
        user = await self.repo.update_by_id(user_id, update)
        if not user:
            raise NotFoundError("User not found")
        return user

    async def list_users(self, pagination: Pagination, search: str | None = None) -> list[dict]:
        filter_ = {"name": {"$regex": search, "$options": "i"}} if search else {}
        return await self.repo.find_many(filter_, skip=pagination.skip, limit=pagination.page_size, sort=[("_id", -1)])

    async def update_admin_fields(self, user_id: str, payload: AdminUserUpdate) -> dict:
        update = {k: v for k, v in payload.model_dump().items() if v is not None}
        user = await self.repo.update_by_id(user_id, update)
        if not user:
            raise NotFoundError("User not found")
        return user

    async def list_addresses(self, user_id: str) -> list[dict]:
        user = await self.get_by_id(user_id)
        return user.get("addresses", [])

    async def add_address(self, user_id: str, payload: AddressCreate) -> dict:
        user = await self.get_by_id(user_id)
        addresses = user.get("addresses", [])
        new_address = {"id": uuid.uuid4().hex, **payload.model_dump()}
        if new_address["is_default"] or not addresses:
            for addr in addresses:
                addr["is_default"] = False
            new_address["is_default"] = True
        addresses.append(new_address)
        await self.repo.update_by_id(user_id, {"addresses": addresses})
        return new_address

    async def update_address(self, user_id: str, address_id: str, payload: AddressUpdate) -> dict:
        user = await self.get_by_id(user_id)
        addresses = user.get("addresses", [])
        target = next((a for a in addresses if a["id"] == address_id), None)
        if not target:
            raise NotFoundError("Address not found")

        updates = {k: v for k, v in payload.model_dump().items() if v is not None}
        target.update(updates)

        if updates.get("is_default"):
            for addr in addresses:
                if addr["id"] != address_id:
                    addr["is_default"] = False

        await self.repo.update_by_id(user_id, {"addresses": addresses})
        return target

    async def delete_address(self, user_id: str, address_id: str) -> None:
        user = await self.get_by_id(user_id)
        addresses = user.get("addresses", [])
        remaining = [a for a in addresses if a["id"] != address_id]
        if len(remaining) == len(addresses):
            raise NotFoundError("Address not found")
        if remaining and not any(a["is_default"] for a in remaining):
            remaining[0]["is_default"] = True
        await self.repo.update_by_id(user_id, {"addresses": remaining})
