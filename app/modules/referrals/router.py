from fastapi import APIRouter, Depends, Query

from app.deps import get_current_user, get_db
from app.modules.loyalty.repository import LoyaltyRepository
from app.modules.loyalty.service import LoyaltyService
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.service import NotificationService
from app.modules.referrals.repository import ReferralRepository
from app.modules.referrals.schemas import ReferralCodeCheckOut, ReferralSummaryOut
from app.modules.referrals.service import ReferralService
from app.modules.users.repository import UserRepository

router = APIRouter(prefix="/referrals", tags=["referrals"])


def build_referral_service(db) -> ReferralService:
    """Also used by the auth router, which attaches a code during signup."""
    notifications = NotificationService(NotificationRepository(db))
    return ReferralService(
        ReferralRepository(db),
        UserRepository(db),
        LoyaltyService(LoyaltyRepository(db), notifications),
        notifications,
    )


def _service(db=Depends(get_db)) -> ReferralService:
    return build_referral_service(db)


@router.get("", response_model=ReferralSummaryOut)
async def get_my_referrals(
    user: dict = Depends(get_current_user), service: ReferralService = Depends(_service)
):
    """The customer's own code and everyone who has used it. Minting the code on
    first read means an account that never shares never gets one."""
    return ReferralSummaryOut(**await service.summary(str(user["_id"])))


@router.get("/check", response_model=ReferralCodeCheckOut)
async def check_referral_code(
    code: str = Query(min_length=1, max_length=32), service: ReferralService = Depends(_service)
):
    """Open to signed-out visitors — the whole point is to validate a code on the
    signup form, before there is an account to authenticate."""
    return ReferralCodeCheckOut(**await service.check_code(code))
