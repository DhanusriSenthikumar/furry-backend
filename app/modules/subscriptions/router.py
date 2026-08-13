from fastapi import APIRouter, Depends, Query

from app.core.pagination import Pagination
from app.deps import (
    get_current_admin,
    get_current_user,
    get_db,
    get_optional_user,
    pagination_params,
)
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.service import NotificationService
from app.modules.orders.router import build_order_service
from app.modules.products.repository import ProductRepository
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.schemas import (
    SubscriptionCreate,
    SubscriptionOfferOut,
    SubscriptionOut,
    SubscriptionRunOut,
    SubscriptionUpdate,
)
from app.modules.subscriptions.service import SubscriptionService
from app.modules.users.repository import UserRepository

# No prefix: the customer's plans live under /subscriptions, the product page's
# offer hangs off /products the way reviews and stock alerts do, and the runner
# belongs under /admin.
router = APIRouter(tags=["subscriptions"])


def _service(db=Depends(get_db)) -> SubscriptionService:
    return SubscriptionService(
        SubscriptionRepository(db),
        ProductRepository(db),
        UserRepository(db),
        build_order_service(db),
        NotificationService(NotificationRepository(db)),
    )


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else None


def subscription_out(doc: dict) -> SubscriptionOut:
    return SubscriptionOut(
        id=str(doc["_id"]),
        user_id=doc["user_id"],
        product_id=doc["product_id"],
        product_name=doc.get("product_name", ""),
        product_slug=doc.get("product_slug", ""),
        product_image=doc.get("product_image", ""),
        unit_price=doc.get("unit_price", 0.0),
        quantity=doc.get("quantity", 1),
        interval_days=doc.get("interval_days", 30),
        discount_percent=doc.get("discount_percent", 0.0),
        estimated_total=doc.get("estimated_total", 0.0),
        status=doc.get("status", "active"),
        next_delivery_at=_iso(doc.get("next_delivery_at")),
        shipping_address=doc["shipping_address"],
        orders_placed=doc.get("orders_placed", 0),
        last_order_id=doc.get("last_order_id"),
        last_ordered_at=_iso(doc.get("last_ordered_at")),
        last_error=doc.get("last_error", "") or "",
        in_stock=doc.get("in_stock", True),
        created_at=_iso(doc["created_at"]) or "",
    )


# ---------------------------------------------------------------------- #
# The offer on the product page
# ---------------------------------------------------------------------- #


@router.get("/products/{product_id}/subscription-offer", response_model=SubscriptionOfferOut)
async def get_subscription_offer(
    product_id: str,
    user: dict | None = Depends(get_optional_user),
    service: SubscriptionService = Depends(_service),
):
    """The saving, the cadences on offer, and whether this customer already
    subscribes. Answered signed-out so the discount is visible before signup."""
    return SubscriptionOfferOut(
        **await service.offer(product_id, str(user["_id"]) if user else None)
    )


# ---------------------------------------------------------------------- #
# The customer's plans
# ---------------------------------------------------------------------- #


@router.get("/subscriptions", response_model=list[SubscriptionOut])
async def list_my_subscriptions(
    status: str | None = None,
    user: dict = Depends(get_current_user),
    service: SubscriptionService = Depends(_service),
):
    return [subscription_out(doc) for doc in await service.list_mine(str(user["_id"]), status)]


@router.post("/subscriptions", response_model=SubscriptionOut, status_code=201)
async def create_subscription(
    payload: SubscriptionCreate,
    user: dict = Depends(get_current_user),
    service: SubscriptionService = Depends(_service),
):
    return subscription_out(await service.create(user, payload))


@router.patch("/subscriptions/{subscription_id}", response_model=SubscriptionOut)
async def update_subscription(
    subscription_id: str,
    payload: SubscriptionUpdate,
    user: dict = Depends(get_current_user),
    service: SubscriptionService = Depends(_service),
):
    """Change the quantity, the cadence, or where it ships. Changing the cadence
    re-bases the next delivery so it takes effect immediately."""
    return subscription_out(await service.update(subscription_id, user, payload))


@router.post("/subscriptions/{subscription_id}/pause", response_model=SubscriptionOut)
async def pause_subscription(
    subscription_id: str,
    user: dict = Depends(get_current_user),
    service: SubscriptionService = Depends(_service),
):
    return subscription_out(await service.pause(subscription_id, user))


@router.post("/subscriptions/{subscription_id}/resume", response_model=SubscriptionOut)
async def resume_subscription(
    subscription_id: str,
    user: dict = Depends(get_current_user),
    service: SubscriptionService = Depends(_service),
):
    return subscription_out(await service.resume(subscription_id, user))


@router.post("/subscriptions/{subscription_id}/skip", response_model=SubscriptionOut)
async def skip_subscription(
    subscription_id: str,
    user: dict = Depends(get_current_user),
    service: SubscriptionService = Depends(_service),
):
    """Push the next delivery out one interval, keeping the plan running."""
    return subscription_out(await service.skip(subscription_id, user))


@router.delete("/subscriptions/{subscription_id}", response_model=SubscriptionOut)
async def cancel_subscription(
    subscription_id: str,
    user: dict = Depends(get_current_user),
    service: SubscriptionService = Depends(_service),
):
    return subscription_out(await service.cancel(subscription_id, user))


# ---------------------------------------------------------------------- #
# Staff
# ---------------------------------------------------------------------- #


@router.get("/admin/subscriptions", response_model=list[SubscriptionOut])
async def list_all_subscriptions(
    status: str | None = None,
    pagination: Pagination = Depends(pagination_params),
    _admin: dict = Depends(get_current_admin),
    service: SubscriptionService = Depends(_service),
):
    return [subscription_out(doc) for doc in await service.list_all(pagination, status)]


@router.get("/admin/subscriptions/stats")
async def subscription_stats(
    _admin: dict = Depends(get_current_admin), service: SubscriptionService = Depends(_service)
):
    return await service.stats()


@router.post("/admin/subscriptions/run", response_model=SubscriptionRunOut)
async def run_due_subscriptions(
    limit: int = Query(default=100, ge=1, le=500),
    _admin: dict = Depends(get_current_admin),
    service: SubscriptionService = Depends(_service),
):
    """Place orders for everything that has fallen due.

    Meant to be driven by a scheduler — one call per hour is plenty — with this
    endpoint as the manual handle. Safe to call at any time and from more than
    one caller at once: due rows are claimed before they are acted on, so a
    delivery is placed exactly once.
    """
    return SubscriptionRunOut(**await service.run_due(limit))
