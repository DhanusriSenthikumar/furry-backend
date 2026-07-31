from pydantic import BaseModel, Field


class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


class ReviewOut(BaseModel):
    id: str
    product_id: str
    user_id: str
    user_name: str
    rating: int
    comment: str
    # Reviews written before verification existed carry no flag; they read as
    # unverified rather than being backfilled into a claim nobody checked.
    verified_purchase: bool = False
    created_at: str


class ReviewEligibilityOut(BaseModel):
    can_review: bool
    reason: str = ""
    verified_purchase: bool = False
    has_reviewed: bool = False
