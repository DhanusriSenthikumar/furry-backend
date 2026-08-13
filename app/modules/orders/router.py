from fastapi import APIRouter, Depends

from app.core.pagination import Pagination
from app.core.shipping import carrier_name, carrier_options
from app.deps import get_current_admin, get_current_user, get_db, pagination_params
from app.modules.coupons.repository import CouponRepository
from app.modules.coupons.service import CouponService
from app.modules.loyalty.repository import LoyaltyRepository
from app.modules.loyalty.service import LoyaltyService
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.service import NotificationService
from app.modules.orders.repository import OrderRepository
from app.modules.orders.schemas import (
    OrderCancel,
    OrderCreate,
    OrderOut,
    OrderQuote,
    OrderQuoteOut,
    OrderStatusUpdate,
    ShipmentCreate,
    ShipmentOut,
)
from app.modules.orders.service import OrderService
from app.modules.products.repository import ProductRepository
from app.modules.referrals.repository import ReferralRepository
from app.modules.referrals.service import ReferralService
from app.modules.stock_alerts.router import build_stock_alert_service
from app.modules.users.repository import UserRepository

router = APIRouter(prefix="/orders", tags=["orders"])


def build_order_service(db) -> OrderService:
    """Assembles the order service with every collaborator it can have.

    Shared with the payments, returns, and subscription routers so that an order
    moved from any of them fires the same emails, the same feed entries, and the
    same rewards settlement. A service built by hand somewhere else would
    silently skip whichever collaborator that call site forgot.
    """
    orders = OrderRepository(db)
    products = ProductRepository(db)
    users = UserRepository(db)
    notifications = NotificationService(NotificationRepository(db))
    loyalty = LoyaltyService(LoyaltyRepository(db), notifications)

    return OrderService(
        orders,
        products,
        CouponService(CouponRepository(db), orders),
        users,
        build_stock_alert_service(db),
        loyalty,
        ReferralService(ReferralRepository(db), users, loyalty, notifications),
        notifications,
    )


def _service(db=Depends(get_db)) -> OrderService:
    return build_order_service(db)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def shipment_out(shipment: dict | None) -> ShipmentOut | None:
    if not shipment:
        return None
    return ShipmentOut(
        carrier=shipment["carrier"],
        carrier_name=carrier_name(shipment["carrier"]),
        tracking_number=shipment.get("tracking_number", ""),
        tracking_url=shipment.get("tracking_url", ""),
        estimated_delivery=shipment.get("estimated_delivery", ""),
        shipped_at=_iso(shipment["shipped_at"]),
    )


def order_out(doc: dict) -> OrderOut:
    history = [{**entry, "at": _iso(entry["at"])} for entry in doc.get("status_history", [])]
    can_return, blocked_reason = OrderService.return_eligibility(doc)
    # Orders placed before totals were itemized only carry `total`; fall back to it
    # so historic orders still render.
    return OrderOut(
        id=str(doc["_id"]),
        user_id=doc["user_id"],
        items=doc["items"],
        shipping_address=doc["shipping_address"],
        subtotal=doc.get("subtotal", doc["total"]),
        discount=doc.get("discount", 0.0),
        coupon_code=doc.get("coupon_code"),
        shipping_fee=doc.get("shipping_fee", 0.0),
        tax=doc.get("tax", 0.0),
        rewards_discount=doc.get("rewards_discount", 0.0),
        redeem_points=doc.get("redeem_points", 0),
        total=doc["total"],
        status=doc["status"],
        status_history=history,
        source=doc.get("source", ""),
        subscription_id=doc.get("subscription_id"),
        can_cancel=OrderService.can_cancel(doc),
        shipment=shipment_out(doc.get("shipment")),
        refunded_amount=doc.get("refunded_amount", 0.0),
        can_return=can_return,
        return_blocked_reason=blocked_reason,
        created_at=_iso(doc["created_at"]),
    )


@router.post("/quote", response_model=OrderQuoteOut)
async def quote_order(
    payload: OrderQuote, user: dict = Depends(get_current_user), service: OrderService = Depends(_service)
):
    """Server-computed cart totals — shipping, tax, and any coupon discount."""
    return OrderQuoteOut(**await service.quote(user, payload))


@router.post("", response_model=OrderOut, status_code=201)
async def checkout(payload: OrderCreate, user: dict = Depends(get_current_user), service: OrderService = Depends(_service)):
    order = await service.checkout(user, payload)
    return order_out(order)


@router.get("", response_model=list[OrderOut])
async def list_my_orders(user: dict = Depends(get_current_user), service: OrderService = Depends(_service)):
    orders = await service.list_mine(str(user["_id"]))
    return [order_out(o) for o in orders]


@router.get("/carriers")
async def list_carriers():
    """Carriers the admin ship form offers. Served from the backend so the
    tracking-URL templates and the picker can never drift apart."""
    return carrier_options()


@router.get("/all", response_model=list[OrderOut])
async def list_all_orders(
    status: str | None = None,
    pagination: Pagination = Depends(pagination_params),
    _admin: dict = Depends(get_current_admin),
    service: OrderService = Depends(_service),
):
    orders = await service.list_all(pagination, status)
    return [order_out(o) for o in orders]


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(order_id: str, user: dict = Depends(get_current_user), service: OrderService = Depends(_service)):
    order = await service.get_owned(order_id, user)
    return order_out(order)


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(
    order_id: str,
    payload: OrderCancel,
    user: dict = Depends(get_current_user),
    service: OrderService = Depends(_service),
):
    """Customer-initiated cancellation. Restores stock while the order is unshipped."""
    order = await service.cancel_own(order_id, user, payload.reason)
    return order_out(order)


@router.patch("/{order_id}/status", response_model=OrderOut)
async def update_order_status(
    order_id: str,
    payload: OrderStatusUpdate,
    _admin: dict = Depends(get_current_admin),
    service: OrderService = Depends(_service),
):
    order = await service.update_status_admin(order_id, payload)
    return order_out(order)


@router.post("/{order_id}/shipment", response_model=OrderOut)
async def ship_order(
    order_id: str,
    payload: ShipmentCreate,
    _admin: dict = Depends(get_current_admin),
    service: OrderService = Depends(_service),
):
    """Record carrier and tracking number, moving the order to shipped and
    emailing the customer the tracking link. Posting again corrects a wrong
    number without re-notifying."""
    order = await service.ship(order_id, payload)
    return order_out(order)
