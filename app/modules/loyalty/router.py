from fastapi import APIRouter, Depends, Query

from app.core.pagination import Pagination
from app.deps import get_current_admin, get_current_user, get_db, pagination_params
from app.modules.loyalty.repository import LoyaltyRepository
from app.modules.loyalty.schemas import (
    LoyaltyAdjust,
    LoyaltyEntryOut,
    LoyaltySummaryOut,
    RedemptionPreviewOut,
)
from app.modules.loyalty.service import LoyaltyService
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.service import NotificationService

# No prefix: the customer's own rewards live under /rewards, and staff tools for
# somebody else's balance belong under /admin.
router = APIRouter(tags=["loyalty"])


def _service(db=Depends(get_db)) -> LoyaltyService:
    return LoyaltyService(LoyaltyRepository(db), NotificationService(NotificationRepository(db)))


def entry_out(doc: dict) -> LoyaltyEntryOut:
    created = doc["created_at"]
    return LoyaltyEntryOut(
        id=str(doc["_id"]),
        points=doc["points"],
        kind=doc.get("kind", "adjustment"),
        reason=doc.get("reason", ""),
        order_id=doc.get("order_id"),
        created_at=created.isoformat() if hasattr(created, "isoformat") else str(created),
    )


def summary_out(data: dict) -> LoyaltySummaryOut:
    return LoyaltySummaryOut(**{**data, "recent": [entry_out(e) for e in data["recent"]]})


@router.get("/rewards", response_model=LoyaltySummaryOut)
async def get_my_rewards(user: dict = Depends(get_current_user), service: LoyaltyService = Depends(_service)):
    """Balance, tier standing, the rate card, and the last few movements."""
    return summary_out(await service.summary(str(user["_id"])))


@router.get("/rewards/history", response_model=list[LoyaltyEntryOut])
async def get_my_rewards_history(
    pagination: Pagination = Depends(pagination_params),
    user: dict = Depends(get_current_user),
    service: LoyaltyService = Depends(_service),
):
    return [entry_out(e) for e in await service.history(str(user["_id"]), pagination)]


@router.get("/rewards/preview", response_model=RedemptionPreviewOut)
async def preview_redemption(
    subtotal: float = Query(ge=0, description="Basket subtotal the points would be spent against"),
    user: dict = Depends(get_current_user),
    service: LoyaltyService = Depends(_service),
):
    """The ceiling checkout should bound its slider to, so a customer is never
    offered a redemption the order endpoint would then clamp."""
    return RedemptionPreviewOut(**await service.preview(str(user["_id"]), subtotal))


# ---------------------------------------------------------------------- #
# Staff
# ---------------------------------------------------------------------- #


@router.get("/admin/rewards/{user_id}", response_model=LoyaltySummaryOut)
async def get_customer_rewards(
    user_id: str, _admin: dict = Depends(get_current_admin), service: LoyaltyService = Depends(_service)
):
    return summary_out(await service.summary(user_id))


@router.post("/admin/rewards/{user_id}/adjust", response_model=LoyaltySummaryOut)
async def adjust_customer_rewards(
    user_id: str,
    payload: LoyaltyAdjust,
    admin: dict = Depends(get_current_admin),
    service: LoyaltyService = Depends(_service),
):
    """Goodwill, or correcting a mistake. Recorded in the ledger with the name of
    whoever made the call, because a balance that moved for no visible reason is
    a support ticket waiting to happen."""
    return summary_out(await service.adjust(user_id, payload.points, payload.reason, admin["name"]))


@router.post("/admin/rewards/{user_id}/reconcile")
async def reconcile_customer_rewards(
    user_id: str, _admin: dict = Depends(get_current_admin), service: LoyaltyService = Depends(_service)
):
    """Rebuild the cached balance from the ledger. The ledger always wins."""
    return await service.reconcile(user_id)
