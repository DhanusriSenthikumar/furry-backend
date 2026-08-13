from fastapi import APIRouter, Depends, Query

from app.core.pagination import Pagination
from app.deps import get_current_admin, get_current_user, get_db, get_optional_user, pagination_params
from app.modules.categories.repository import CategoryRepository
from app.modules.orders.repository import OrderRepository
from app.modules.pets.repository import PetRepository
from app.modules.products.repository import ProductRepository
from app.modules.products.schemas import (
    PlantDetails,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    SuggestionOut,
)
from app.modules.products.service import ProductService
from app.modules.recommendations.repository import ProductViewRepository
from app.modules.recommendations.schemas import RecommendationOut, RecommendedItem
from app.modules.recommendations.service import RecommendationService
from app.modules.reviews.repository import ReviewRepository
from app.modules.reviews.schemas import ReviewCreate, ReviewEligibilityOut, ReviewOut
from app.modules.reviews.service import ReviewService
from app.modules.stock_alerts.router import build_stock_alert_service
from app.modules.stock_alerts.service import StockAlertService
from app.modules.users.repository import UserRepository

router = APIRouter(tags=["products"])


def _alert_service(db) -> StockAlertService:
    """Built alongside the product service so an admin stock edit tells whoever
    has been waiting for that product."""
    return build_stock_alert_service(db)


def _service(db=Depends(get_db)) -> ProductService:
    return ProductService(ProductRepository(db), CategoryRepository(db), _alert_service(db))


def _review_service(db=Depends(get_db)) -> ReviewService:
    # Orders come along so a review can be checked against a real delivery.
    return ReviewService(ReviewRepository(db), ProductRepository(db), OrderRepository(db))


def _recommendation_service(db=Depends(get_db)) -> RecommendationService:
    return RecommendationService(
        ProductRepository(db), PetRepository(db), OrderRepository(db), ProductViewRepository(db)
    )


def product_out(doc: dict) -> ProductOut:
    plant = doc.get("plant")
    price = doc["price"]
    # A compare price only survives the service normalizer when it is a real
    # saving, so this stays a straight subtraction.
    compare_at = doc.get("compare_at_price", 0) or 0
    on_sale = compare_at > price > 0
    return ProductOut(
        id=str(doc["_id"]),
        name=doc["name"],
        slug=doc["slug"],
        description=doc.get("description", ""),
        category_id=doc["category_id"],
        category_name=doc.get("category_name", ""),
        product_kind=doc.get("product_kind", "pet"),
        suitable_pet_types=doc.get("suitable_pet_types", []),
        plant=PlantDetails(**plant) if plant else None,
        brand=doc.get("brand", ""),
        price=price,
        compare_at_price=compare_at,
        discount_percent=round((1 - price / compare_at) * 100) if on_sale else 0,
        on_sale=on_sale,
        images=doc.get("images", []),
        stock=doc.get("stock", 0),
        rating=doc.get("rating", 0.0),
        rating_count=doc.get("rating_count", 0),
    )


def review_out(doc: dict) -> ReviewOut:
    return ReviewOut(
        id=str(doc["_id"]),
        product_id=doc["product_id"],
        user_id=doc["user_id"],
        user_name=doc["user_name"],
        rating=doc["rating"],
        comment=doc.get("comment", ""),
        verified_purchase=doc.get("verified_purchase", False),
        created_at=doc["created_at"].isoformat() if hasattr(doc["created_at"], "isoformat") else doc["created_at"],
    )


@router.get("/products", response_model=list[ProductOut])
async def list_products(
    category_id: str | None = None,
    pet_type: str | None = None,
    q: str | None = None,
    sort: str | None = None,
    brand: str | None = None,
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    in_stock: bool = False,
    product_kind: str | None = Query(default=None, pattern="^(pet|plant)$"),
    plant_type: str | None = None,
    light_needs: str | None = None,
    water_needs: str | None = None,
    care_level: str | None = None,
    pot_included: bool = False,
    min_rating: float | None = Query(default=None, ge=0, le=5),
    on_sale: bool = False,
    pagination: Pagination = Depends(pagination_params),
    service: ProductService = Depends(_service),
):
    products = await service.list_products(
        pagination,
        category_id=category_id,
        pet_type=pet_type,
        q=q,
        sort=sort,
        brand=brand,
        min_price=min_price,
        max_price=max_price,
        in_stock=in_stock,
        product_kind=product_kind,
        plant_type=plant_type,
        light_needs=light_needs,
        water_needs=water_needs,
        care_level=care_level,
        pot_included=pot_included,
        min_rating=min_rating,
        on_sale=on_sale,
    )
    return [product_out(p) for p in products]


@router.get("/products/brands", response_model=list[str])
async def list_brands(
    product_kind: str | None = Query(default=None, pattern="^(pet|plant)$"),
    service: ProductService = Depends(_service),
):
    """Brand facet, scoped to one catalogue line so plant nurseries don't show
    up under pet supplies and vice versa."""
    return await service.list_brands(product_kind)


@router.get("/products/suggest", response_model=list[SuggestionOut])
async def suggest_products(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=8, ge=1, le=20),
    product_kind: str | None = Query(default=None, pattern="^(pet|plant)$"),
    service: ProductService = Depends(_service),
):
    """Search typeahead. Registered before /products/{slug} so it isn't shadowed."""
    docs = await service.suggest(q, limit=limit, product_kind=product_kind)
    return [
        SuggestionOut(
            name=doc["name"],
            slug=doc["slug"],
            price=doc["price"],
            image=(doc.get("images") or [""])[0],
            product_kind=doc.get("product_kind", "pet"),
        )
        for doc in docs
    ]


@router.get("/products/recommended", response_model=RecommendationOut)
async def recommended_products(
    user: dict = Depends(get_current_user),
    service: RecommendationService = Depends(_recommendation_service),
):
    """Personalized picks, ranked against the user's pets, order history, and
    browsing history. Each pick carries the signal that earned it."""
    result = await service.recommend(user, limit=8)
    return RecommendationOut(
        pet_types=result["pet_types"],
        pet_names=result["pet_names"],
        items=[
            RecommendedItem(product=product_out(item["product"]), reason=item["reason"])
            for item in result["items"]
        ],
        personalized=result["personalized"],
    )


@router.get("/products/recently-viewed", response_model=list[ProductOut])
async def recently_viewed_products(
    limit: int = Query(default=12, ge=1, le=50),
    user: dict = Depends(get_current_user),
    service: RecommendationService = Depends(_recommendation_service),
):
    """The customer's own browsing history, most recent first. Registered before
    /products/{slug} so the literal path isn't swallowed by the slug route."""
    products = await service.recently_viewed(str(user["_id"]), limit=limit)
    return [product_out(p) for p in products]


@router.get("/products/{slug}", response_model=ProductOut)
async def get_product(slug: str, service: ProductService = Depends(_service)):
    product = await service.get_by_slug(slug)
    return product_out(product)


@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(
    payload: ProductCreate, _admin: dict = Depends(get_current_admin), service: ProductService = Depends(_service)
):
    product = await service.create(payload)
    return product_out(product)


@router.put("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: str,
    payload: ProductUpdate,
    _admin: dict = Depends(get_current_admin),
    service: ProductService = Depends(_service),
):
    product = await service.update(product_id, payload)
    return product_out(product)


@router.delete("/products/{product_id}", status_code=204)
async def delete_product(
    product_id: str, _admin: dict = Depends(get_current_admin), service: ProductService = Depends(_service)
):
    await service.delete(product_id)


@router.post("/products/{product_id}/view", status_code=204)
async def record_product_view(
    product_id: str,
    user: dict = Depends(get_current_user),
    service: RecommendationService = Depends(_recommendation_service),
):
    """Records that the signed-in customer looked at a product. Feeds both the
    recently-viewed rail and the affinity signals behind /products/recommended."""
    await service.record_view(str(user["_id"]), product_id)


@router.get("/products/{product_id}/reviews", response_model=list[ReviewOut])
async def list_product_reviews(product_id: str, service: ReviewService = Depends(_review_service)):
    reviews = await service.list_for_product(product_id)
    return [review_out(r) for r in reviews]


@router.get("/products/{product_id}/reviews/eligibility", response_model=ReviewEligibilityOut)
async def review_eligibility(
    product_id: str,
    user: dict | None = Depends(get_optional_user),
    service: ReviewService = Depends(_review_service),
):
    """Whether the caller can review this product. Answers for signed-out
    visitors too, so the page can invite them to sign in rather than showing a
    form that will be refused."""
    return ReviewEligibilityOut(**await service.eligibility(product_id, user))


@router.post("/products/{product_id}/reviews", response_model=ReviewOut, status_code=201)
async def submit_product_review(
    product_id: str,
    payload: ReviewCreate,
    user: dict = Depends(get_current_user),
    service: ReviewService = Depends(_review_service),
):
    review = await service.submit_review(product_id, user, payload)
    return review_out(review)
