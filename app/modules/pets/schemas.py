from pydantic import BaseModel, Field

from app.core.enums import Gender, PetType


class PetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    pet_type: PetType
    breed: str = ""
    age_years: float = Field(ge=0, le=50, default=0)
    weight_kg: float = Field(ge=0, le=200, default=0)
    gender: Gender = "unknown"
    special_requirements: str = Field(default="", max_length=1000)


class PetUpdate(BaseModel):
    name: str | None = None
    pet_type: PetType | None = None
    breed: str | None = None
    age_years: float | None = Field(default=None, ge=0, le=50)
    weight_kg: float | None = Field(default=None, ge=0, le=200)
    gender: Gender | None = None
    special_requirements: str | None = None


class PetOut(BaseModel):
    id: str
    owner_id: str
    name: str
    pet_type: PetType
    breed: str
    age_years: float
    weight_kg: float
    gender: Gender
    special_requirements: str
