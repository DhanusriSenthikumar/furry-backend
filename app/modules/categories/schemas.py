from pydantic import BaseModel, Field

from app.core.enums import CategoryKind


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=100)
    description: str = ""
    kind: CategoryKind = "pet"


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    kind: CategoryKind | None = None


class CategoryOut(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    kind: CategoryKind
