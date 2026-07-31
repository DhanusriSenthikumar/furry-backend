from fastapi import APIRouter, Depends, Query

from app.deps import get_current_admin, get_current_user, get_db, get_optional_user
from app.modules.products.repository import ProductRepository
from app.modules.stock_alerts.repository import StockAlertRepository
from app.modules.stock_alerts.schemas import StockAlertDemandOut, StockAlertStatusOut
from app.modules.stock_alerts.service import StockAlertService
from app.modules.users.repository import UserRepository

# No prefix: the customer-facing routes hang off /products the way reviews do,
# and the demand report belongs under /admin.
router = APIRouter(tags=["stock-alerts"])


def _service(db=Depends(get_db)) -> StockAlertService:
    return StockAlertService(StockAlertRepository(db), ProductRepository(db), UserRepository(db))


@router.get("/products/{product_id}/stock-alert", response_model=StockAlertStatusOut)
async def get_stock_alert(
    product_id: str,
    user: dict | None = Depends(get_optional_user),
    service: StockAlertService = Depends(_service),
):
    """Answerable signed out — a visitor still sees how many people are waiting,
    which is the honest version of "in demand"."""
    return StockAlertStatusOut(**await service.status(product_id, str(user["_id"]) if user else None))


@router.post("/products/{product_id}/stock-alert", response_model=StockAlertStatusOut, status_code=201)
async def create_stock_alert(
    product_id: str,
    user: dict = Depends(get_current_user),
    service: StockAlertService = Depends(_service),
):
    return StockAlertStatusOut(**await service.subscribe(product_id, str(user["_id"])))


@router.delete("/products/{product_id}/stock-alert", status_code=204)
async def delete_stock_alert(
    product_id: str,
    user: dict = Depends(get_current_user),
    service: StockAlertService = Depends(_service),
):
    await service.unsubscribe(product_id, str(user["_id"]))


@router.get("/admin/stock-alerts", response_model=list[StockAlertDemandOut])
async def list_stock_alert_demand(
    limit: int = Query(default=50, ge=1, le=200),
    _admin: dict = Depends(get_current_admin),
    service: StockAlertService = Depends(_service),
):
    """What customers are waiting for, most-wanted first — the restock queue
    ordered by demand rather than by how empty the shelf is."""
    return [StockAlertDemandOut(**row) for row in await service.demand(limit)]
