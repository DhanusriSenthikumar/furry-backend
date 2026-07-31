from pydantic import BaseModel

from app.core.enums import PetType
from app.modules.products.schemas import ProductOut


class RecommendedItem(BaseModel):
    """One pick, with the signal that earned it a place in the list."""

    product: ProductOut
    reason: str


class RecommendationOut(BaseModel):
    """Personalized picks for the signed-in user, matched against their pet
    profiles, what they have bought before, and what they have been browsing."""

    pet_types: list[PetType]
    pet_names: list[str]
    items: list[RecommendedItem]
    # True once there is order or browsing history to learn from, so the UI can
    # say "picked for Rex" rather than implying more personalization than exists.
    personalized: bool = False
