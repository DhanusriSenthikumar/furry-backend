from pydantic import BaseModel, Field

from app.core.enums import CareLevel, LightNeed, PetType, PlantType, ProductKind, WaterNeed


class PlantDetails(BaseModel):
    """Care attributes that only apply when product_kind is "plant"."""

    plant_type: PlantType = "indoor"
    light_needs: LightNeed = "medium"
    water_needs: WaterNeed = "medium"
    care_level: CareLevel = "easy"
    mature_height_cm: int = Field(ge=0, le=3000, default=0)
    pot_included: bool = False
    botanical_name: str = Field(default="", max_length=200)


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=200)
    description: str = ""
    category_id: str
    product_kind: ProductKind = "pet"
    suitable_pet_types: list[PetType] = []
    plant: PlantDetails | None = None
    brand: str = ""
    price: float = Field(gt=0)
    # The "was" price. 0 means "not on sale"; anything at or below `price` is
    # normalized back to 0 by the service, so a sale badge always means a saving.
    compare_at_price: float = Field(ge=0, default=0)
    images: list[str] = []
    stock: int = Field(ge=0, default=0)


class ProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category_id: str | None = None
    product_kind: ProductKind | None = None
    suitable_pet_types: list[PetType] | None = None
    plant: PlantDetails | None = None
    brand: str | None = None
    price: float | None = Field(default=None, gt=0)
    compare_at_price: float | None = Field(default=None, ge=0)
    images: list[str] | None = None
    stock: int | None = Field(default=None, ge=0)


class ProductOut(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    category_id: str
    category_name: str
    product_kind: ProductKind
    suitable_pet_types: list[PetType]
    plant: PlantDetails | None
    brand: str
    price: float
    compare_at_price: float = 0
    # Derived from the two prices so every surface rounds the saving the same way.
    discount_percent: int = 0
    on_sale: bool = False
    images: list[str]
    stock: int
    rating: float
    rating_count: int


class SuggestionOut(BaseModel):
    """Just enough for the search typeahead — deliberately not a full ProductOut."""

    name: str
    slug: str
    price: float
    image: str = ""
    product_kind: ProductKind = "pet"
