from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.pets.repository import PetRepository
from app.modules.pets.schemas import PetCreate, PetUpdate


class PetService:
    def __init__(self, repo: PetRepository):
        self.repo = repo

    async def list_mine(self, owner_id: str) -> list[dict]:
        return await self.repo.find_by_owner(owner_id)

    async def get_owned(self, pet_id: str, owner_id: str) -> dict:
        pet = await self.repo.find_by_id(pet_id)
        if not pet:
            raise NotFoundError("Pet not found")
        if pet["owner_id"] != owner_id:
            raise ForbiddenError("Not allowed to access this pet")
        return pet

    async def create(self, owner_id: str, payload: PetCreate) -> dict:
        doc = {"owner_id": owner_id, **payload.model_dump()}
        return await self.repo.insert(doc)

    async def update(self, pet_id: str, owner_id: str, payload: PetUpdate) -> dict:
        await self.get_owned(pet_id, owner_id)
        update = {k: v for k, v in payload.model_dump().items() if v is not None}
        return await self.repo.update_by_id(pet_id, update)

    async def delete(self, pet_id: str, owner_id: str) -> None:
        await self.get_owned(pet_id, owner_id)
        await self.repo.delete_by_id(pet_id)
