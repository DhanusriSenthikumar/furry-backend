from fastapi import APIRouter, Depends

from app.core.pagination import Pagination
from app.deps import get_current_admin, get_current_user, get_db, pagination_params
from app.modules.coupons.repository import CouponRepository
from app.modules.coupons.service import CouponService
from app.modules.orders.repository import OrderRepository
from app.modules.orders.service import OrderService
from app.modules.payments.razorpay_client import razorpay_client
from app.modules.payments.repository import PaymentRepository
from app.modules.payments.stripe_client import stripe_client
from app.modules.products.repository import ProductRepository
from app.modules.returns.repository import ReturnRepository
from app.modules.returns.schemas import (
    RefundBreakdownOut,
    ReturnCreate,
    ReturnEligibilityOut,
    ReturnOut,
    ReturnRefund,
    ReturnResolve,
)
from app.modules.returns.service import ReturnService
from app.modules.stock_alerts.repository import StockAlertRepository
from app.modules.stock_alerts.service import StockAlertService
from app.modules.users.repository import UserRepository

router = APIRouter(prefix="/returns", tags=["returns"])


def _service(db=Depends(get_db)) -> ReturnService:
    orders_repo = OrderRepository(db)
    alerts = StockAlertService(StockAlertRepository(db), ProductRepository(db), UserRepository(db))
    order_service = OrderService(
        orders_repo,
        ProductRepository(db),
        CouponService(CouponRepository(db), orders_repo),
        UserRepository(db),
        alerts,
    )
    return ReturnService(
        ReturnRepository(db),
        order_service,
        ProductRepository(db),
        UserRepository(db),
        PaymentRepository(db),
        stripe_client,
        razorpay_client,
        alerts,
    )


def _iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else value


def return_out(doc: dict) -> ReturnOut:
    return ReturnOut(
        id=str(doc["_id"]),
        user_id=doc["user_id"],
        order_id=doc["order_id"],
        order_ref=doc.get("order_ref", doc["order_id"][-8:]),
        items=doc["items"],
        comment=doc.get("comment", ""),
        status=doc["status"],
        refund_estimate=doc.get("refund_estimate", 0.0),
        refund_breakdown=RefundBreakdownOut(**doc["refund_breakdown"]),
        refund_amount=doc.get("refund_amount", 0.0),
        refund_method=doc.get("refund_method", ""),
        refund_reference=doc.get("refund_reference", ""),
        restocked=doc.get("restocked", False),
        resolution_note=doc.get("resolution_note", ""),
        resolved_by=doc.get("resolved_by", ""),
        created_at=_iso(doc["created_at"]),
        updated_at=_iso(doc.get("updated_at", doc["created_at"])),
    )


@router.get("", response_model=list[ReturnOut])
async def list_my_returns(user: dict = Depends(get_current_user), service: ReturnService = Depends(_service)):
    returns = await service.list_mine(str(user["_id"]))
    return [return_out(r) for r in returns]


@router.get("/all", response_model=list[ReturnOut])
async def list_all_returns(
    status: str | None = None,
    pagination: Pagination = Depends(pagination_params),
    _admin: dict = Depends(get_current_admin),
    service: ReturnService = Depends(_service),
):
    """The staff queue. Filter by `status=requested` for the ones awaiting a decision."""
    returns = await service.list_all(pagination, status)
    return [return_out(r) for r in returns]


@router.get("/eligibility/{order_id}", response_model=ReturnEligibilityOut)
async def check_eligibility(
    order_id: str, user: dict = Depends(get_current_user), service: ReturnService = Depends(_service)
):
    """Whether this order can be returned, and how much of each line is left —
    so the form can explain itself instead of failing on submit."""
    return ReturnEligibilityOut(**await service.eligibility(order_id, user))


@router.post("", response_model=ReturnOut, status_code=201)
async def request_return(
    payload: ReturnCreate, user: dict = Depends(get_current_user), service: ReturnService = Depends(_service)
):
    return return_out(await service.request(user, payload))


@router.get("/{return_id}", response_model=ReturnOut)
async def get_return(
    return_id: str, user: dict = Depends(get_current_user), service: ReturnService = Depends(_service)
):
    return return_out(await service.get_owned(return_id, user))


@router.post("/{return_id}/approve", response_model=ReturnOut)
async def approve_return(
    return_id: str,
    payload: ReturnResolve,
    admin: dict = Depends(get_current_admin),
    service: ReturnService = Depends(_service),
):
    return return_out(await service.approve(return_id, admin, payload))


@router.post("/{return_id}/reject", response_model=ReturnOut)
async def reject_return(
    return_id: str,
    payload: ReturnResolve,
    admin: dict = Depends(get_current_admin),
    service: ReturnService = Depends(_service),
):
    return return_out(await service.reject(return_id, admin, payload))


@router.post("/{return_id}/refund", response_model=ReturnOut)
async def refund_return(
    return_id: str,
    payload: ReturnRefund,
    admin: dict = Depends(get_current_admin),
    service: ReturnService = Depends(_service),
):
    """Settle an approved return. Restores stock unless `restock: false`, pushes
    the money back through the original gateway when one is configured, and marks
    the order refunded once nothing is left owing."""
    return return_out(await service.refund(return_id, admin, payload))
