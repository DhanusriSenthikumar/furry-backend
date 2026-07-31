from fastapi import APIRouter, Depends, Query

from app.deps import get_current_admin, get_db
from app.modules.categories.repository import CategoryRepository
from app.modules.categories.schemas import CategoryCreate, CategoryOut, CategoryUpdate
from app.modules.categories.service import CategoryService
from app.modules.products.repository import ProductRepository

router = APIRouter(prefix="/categories", tags=["categories"])


def _service(db=Depends(get_db)) -> CategoryService:
    return CategoryService(CategoryRepository(db), ProductRepository(db))


def category_out(doc: dict) -> CategoryOut:
    return CategoryOut(
        id=str(doc["_id"]),
        name=doc["name"],
        slug=doc["slug"],
        description=doc.get("description", ""),
        kind=doc.get("kind", "pet"),
    )


@router.get("", response_model=list[CategoryOut])
async def list_categories(
    kind: str | None = Query(default=None, pattern="^(pet|plant)$"),
    service: CategoryService = Depends(_service),
):
    categories = await service.list_all(kind)
    return [category_out(c) for c in categories]


@router.get("/{category_id}", response_model=CategoryOut)
async def get_category(category_id: str, service: CategoryService = Depends(_service)):
    category = await service.get_by_id(category_id)
    return category_out(category)


@router.post("", response_model=CategoryOut, status_code=201)
async def create_category(
    payload: CategoryCreate, _admin: dict = Depends(get_current_admin), service: CategoryService = Depends(_service)
):
    category = await service.create(payload)
    return category_out(category)


@router.put("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: str,
    payload: CategoryUpdate,
    _admin: dict = Depends(get_current_admin),
    service: CategoryService = Depends(_service),
):
    category = await service.update(category_id, payload)
    return category_out(category)


@router.delete("/{category_id}", status_code=204)
async def delete_category(
    category_id: str, _admin: dict = Depends(get_current_admin), service: CategoryService = Depends(_service)
):
    await service.delete(category_id)
