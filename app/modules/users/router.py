from fastapi import APIRouter, Depends

from app.core.loyalty import tier_for
from app.core.pagination import Pagination
from app.deps import get_current_admin, get_current_user, get_db, pagination_params
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import (
    AddressCreate,
    AddressOut,
    AddressUpdate,
    AdminUserUpdate,
    UserOut,
    UserUpdate,
)
from app.modules.users.service import UserService

router = APIRouter(tags=["users"])


def _service(db=Depends(get_db)) -> UserService:
    return UserService(UserRepository(db))


def user_out(doc: dict) -> UserOut:
    tier, _multiplier = tier_for(int(doc.get("loyalty_lifetime_points", 0) or 0))
    return UserOut(
        id=str(doc["_id"]),
        name=doc["name"],
        email=doc["email"],
        is_admin=doc.get("is_admin", False),
        is_active=doc.get("is_active", True),
        loyalty_points=int(doc.get("loyalty_points", 0) or 0),
        loyalty_tier=tier,
    )


def address_out(doc: dict) -> AddressOut:
    return AddressOut(**doc)


@router.get("/users/me", response_model=UserOut)
async def get_me(user: dict = Depends(get_current_user)):
    return user_out(user)


@router.patch("/users/me", response_model=UserOut)
async def update_me(payload: UserUpdate, user: dict = Depends(get_current_user), service: UserService = Depends(_service)):
    updated = await service.update_profile(str(user["_id"]), payload)
    return user_out(updated)


@router.get("/users/me/addresses", response_model=list[AddressOut])
async def list_my_addresses(user: dict = Depends(get_current_user), service: UserService = Depends(_service)):
    addresses = await service.list_addresses(str(user["_id"]))
    return [address_out(a) for a in addresses]


@router.post("/users/me/addresses", response_model=AddressOut, status_code=201)
async def add_my_address(
    payload: AddressCreate, user: dict = Depends(get_current_user), service: UserService = Depends(_service)
):
    address = await service.add_address(str(user["_id"]), payload)
    return address_out(address)


@router.patch("/users/me/addresses/{address_id}", response_model=AddressOut)
async def update_my_address(
    address_id: str,
    payload: AddressUpdate,
    user: dict = Depends(get_current_user),
    service: UserService = Depends(_service),
):
    address = await service.update_address(str(user["_id"]), address_id, payload)
    return address_out(address)


@router.delete("/users/me/addresses/{address_id}", status_code=204)
async def delete_my_address(
    address_id: str, user: dict = Depends(get_current_user), service: UserService = Depends(_service)
):
    await service.delete_address(str(user["_id"]), address_id)


@router.get("/users", response_model=list[UserOut])
async def list_users(
    search: str | None = None,
    pagination: Pagination = Depends(pagination_params),
    _admin: dict = Depends(get_current_admin),
    service: UserService = Depends(_service),
):
    users = await service.list_users(pagination, search)
    return [user_out(u) for u in users]


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(user_id: str, _admin: dict = Depends(get_current_admin), service: UserService = Depends(_service)):
    user = await service.get_by_id(user_id)
    return user_out(user)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user_admin_fields(
    user_id: str,
    payload: AdminUserUpdate,
    _admin: dict = Depends(get_current_admin),
    service: UserService = Depends(_service),
):
    updated = await service.update_admin_fields(user_id, payload)
    return user_out(updated)
