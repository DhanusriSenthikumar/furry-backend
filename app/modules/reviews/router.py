from fastapi import APIRouter, Depends

from app.deps import get_current_user, get_db
from app.modules.products.repository import ProductRepository
from app.modules.reviews.repository import ReviewRepository
from app.modules.reviews.service import ReviewService

router = APIRouter(prefix="/reviews", tags=["reviews"])


def _service(db=Depends(get_db)) -> ReviewService:
    return ReviewService(ReviewRepository(db), ProductRepository(db))


@router.delete("/{review_id}", status_code=204)
async def delete_review(review_id: str, user: dict = Depends(get_current_user), service: ReviewService = Depends(_service)):
    await service.delete_review(review_id, user)
