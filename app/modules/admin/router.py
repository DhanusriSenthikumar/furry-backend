from fastapi import APIRouter, Depends

from app.deps import get_current_admin, get_db
from app.modules.admin.schemas import AdminStats
from app.modules.admin.service import AdminService
from app.modules.orders.repository import OrderRepository
from app.modules.products.repository import ProductRepository
from app.modules.returns.repository import ReturnRepository
from app.modules.users.repository import UserRepository

router = APIRouter(prefix="/admin", tags=["admin"])


def _service(db=Depends(get_db)) -> AdminService:
    return AdminService(
        OrderRepository(db), UserRepository(db), ProductRepository(db), ReturnRepository(db)
    )


@router.get("/stats", response_model=AdminStats)
async def get_stats(_admin: dict = Depends(get_current_admin), service: AdminService = Depends(_service)):
    stats = await service.get_stats()
    return AdminStats(**stats)
