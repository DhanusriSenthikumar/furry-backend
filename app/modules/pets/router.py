from fastapi import APIRouter, Depends

from app.deps import get_current_user, get_db
from app.modules.pets.repository import PetRepository
from app.modules.pets.schemas import PetCreate, PetOut, PetUpdate
from app.modules.pets.service import PetService

router = APIRouter(prefix="/pets", tags=["pets"])


def _service(db=Depends(get_db)) -> PetService:
    return PetService(PetRepository(db))


def pet_out(doc: dict) -> PetOut:
    return PetOut(
        id=str(doc["_id"]),
        owner_id=doc["owner_id"],
        name=doc["name"],
        pet_type=doc["pet_type"],
        breed=doc.get("breed", ""),
        age_years=doc.get("age_years", 0),
        weight_kg=doc.get("weight_kg", 0),
        gender=doc.get("gender", "unknown"),
        special_requirements=doc.get("special_requirements", ""),
    )


@router.get("", response_model=list[PetOut])
async def list_my_pets(user: dict = Depends(get_current_user), service: PetService = Depends(_service)):
    pets = await service.list_mine(str(user["_id"]))
    return [pet_out(p) for p in pets]


@router.post("", response_model=PetOut, status_code=201)
async def create_pet(
    payload: PetCreate, user: dict = Depends(get_current_user), service: PetService = Depends(_service)
):
    pet = await service.create(str(user["_id"]), payload)
    return pet_out(pet)


@router.get("/{pet_id}", response_model=PetOut)
async def get_pet(pet_id: str, user: dict = Depends(get_current_user), service: PetService = Depends(_service)):
    pet = await service.get_owned(pet_id, str(user["_id"]))
    return pet_out(pet)


@router.put("/{pet_id}", response_model=PetOut)
async def update_pet(
    pet_id: str, payload: PetUpdate, user: dict = Depends(get_current_user), service: PetService = Depends(_service)
):
    pet = await service.update(pet_id, str(user["_id"]), payload)
    return pet_out(pet)


@router.delete("/{pet_id}", status_code=204)
async def delete_pet(pet_id: str, user: dict = Depends(get_current_user), service: PetService = Depends(_service)):
    await service.delete(pet_id, str(user["_id"]))
