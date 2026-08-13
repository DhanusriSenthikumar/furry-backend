"""Refer-a-friend.

Both sides are paid in loyalty points, and neither is paid at signup — the
invite only settles when the newcomer's first order is actually *delivered*.
That single rule is what keeps the programme from being a faucet: creating
accounts costs nothing, so a reward for creating one would be free money, while
a reward for receiving a parcel is a reward for a real customer.

Attribution is permanent and one-way. An account can be referred once, ever, and
never by itself.
"""

from datetime import datetime, timezone

from app.core.config import settings
from app.modules.loyalty.service import LoyaltyService
from app.modules.notifications.service import NotificationService
from app.modules.referrals.repository import ReferralRepository
from app.modules.users.repository import UserRepository


def _first_name(name: str) -> str:
    return (name or "").strip().split(" ")[0] or "A customer"


def _mask_email(email: str) -> str:
    """j***@example.com — enough to recognise someone you invited, not enough to
    harvest an address list by inviting strangers."""
    local, _, domain = (email or "").partition("@")
    if not domain:
        return ""
    head = local[:1] if local else ""
    return f"{head}{'*' * max(len(local) - 1, 1)}@{domain}"


class ReferralService:
    def __init__(
        self,
        repo: ReferralRepository,
        users: UserRepository,
        loyalty: LoyaltyService | None = None,
        notifications: NotificationService | None = None,
    ):
        self.repo = repo
        self.users = users
        # The programme pays in points, so without a ledger it can still track
        # invites but has nothing to award.
        self.loyalty = loyalty
        self.notifications = notifications

    # ------------------------------------------------------------------ #
    # The referrer's side
    # ------------------------------------------------------------------ #

    async def summary(self, user_id: str) -> dict:
        code = await self.repo.claim_code(user_id)
        invites = await self.repo.find_by_referrer(user_id)
        counts = await self.repo.counts_for(user_id)
        rewarded = counts.get("rewarded", 0)

        return {
            "enabled": settings.referrals_enabled,
            "code": code,
            "share_url": f"{settings.frontend_url}/signup?ref={code}",
            "referrer_points": settings.referral_referrer_points,
            "referee_points": settings.referral_referee_points,
            "invited": sum(counts.values()),
            "pending": counts.get("pending", 0),
            "rewarded": rewarded,
            "points_earned": rewarded * settings.referral_referrer_points,
            "invites": [self._invite_out(invite) for invite in invites],
        }

    @staticmethod
    def _invite_out(invite: dict) -> dict:
        created = invite["created_at"]
        rewarded_at = invite.get("rewarded_at")
        return {
            "id": str(invite["_id"]),
            "referee_name": _first_name(invite.get("referee_name", "")),
            "referee_email": _mask_email(invite.get("referee_email", "")),
            "status": invite.get("status", "pending"),
            "points_earned": (
                settings.referral_referrer_points if invite.get("status") == "rewarded" else 0
            ),
            "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
            "rewarded_at": rewarded_at.isoformat() if hasattr(rewarded_at, "isoformat") else None,
        }

    # ------------------------------------------------------------------ #
    # The newcomer's side
    # ------------------------------------------------------------------ #

    async def check_code(self, code: str) -> dict:
        code = (code or "").strip().upper()
        if not settings.referrals_enabled:
            return {"code": code, "valid": False, "reason": "Referrals are not running at the moment"}
        if not code:
            return {"code": "", "valid": False, "reason": "Enter a code"}

        referrer = await self.repo.find_user_by_code(code)
        if not referrer or not referrer.get("is_active", True):
            return {"code": code, "valid": False, "reason": "We don't recognise that code"}

        return {
            "code": code,
            "valid": True,
            "referrer_name": _first_name(referrer.get("name", "")),
            "referee_points": settings.referral_referee_points,
            "reason": "",
        }

    async def attach(self, new_user: dict, code: str | None) -> None:
        """Record who invited a newly created account.

        Never raises. A bad code is a reason to skip the bonus, never a reason to
        fail a signup the customer has already completed.
        """
        if not code or not settings.referrals_enabled:
            return

        try:
            referrer = await self.repo.find_user_by_code(code)
            referee_id = str(new_user["_id"])
            if not referrer or not referrer.get("is_active", True):
                return
            if str(referrer["_id"]) == referee_id:
                return

            await self.repo.record(
                {
                    "code": code.strip().upper(),
                    "referrer_id": str(referrer["_id"]),
                    "referee_id": referee_id,
                    "referee_name": new_user.get("name", ""),
                    "referee_email": new_user.get("email", ""),
                    "status": "pending",
                    "order_id": None,
                    "created_at": datetime.now(timezone.utc),
                    "rewarded_at": None,
                }
            )
            await self.users.update_by_id(referee_id, {"referred_by": str(referrer["_id"])})

            if self.notifications is not None:
                await self.notifications.push(
                    str(referrer["_id"]),
                    "referral",
                    f"{_first_name(new_user.get('name', ''))} joined with your code",
                    f"You'll both get points once their first order is delivered.",
                    "/account/referrals",
                    dedupe_key=f"referral:joined:{referee_id}",
                )
        except Exception as exc:
            print(f"Warning: could not attach referral code {code!r}: {exc}")

    # ------------------------------------------------------------------ #
    # Settlement
    # ------------------------------------------------------------------ #

    async def qualify(self, referee_id: str, order_id: str) -> bool:
        """Pay both sides once the newcomer's first order is delivered.

        Called on every delivery; the `pending` guard in `mark_rewarded` makes
        every call after the first a no-op, so the caller doesn't have to know
        whether this is a customer's first order.
        """
        if not settings.referrals_enabled or self.loyalty is None:
            return False

        try:
            referral = await self.repo.find_by_referee(referee_id)
            if not referral or referral.get("status") != "pending":
                return False

            settled = await self.repo.mark_rewarded(
                str(referral["_id"]), order_id, datetime.now(timezone.utc)
            )
            if settled is None:
                # Another delivery got there first.
                return False

            referral_id = str(referral["_id"])
            referee_name = _first_name(referral.get("referee_name", ""))

            await self.loyalty.credit_bonus(
                referral["referrer_id"],
                settings.referral_referrer_points,
                "referral",
                f"{referee_name} placed their first order",
                dedupe_key=f"referral:referrer:{referral_id}",
                notify_title=f"{referee_name}'s first order arrived — here are your points",
            )
            await self.loyalty.credit_bonus(
                referee_id,
                settings.referral_referee_points,
                "referral",
                "Welcome bonus for joining with a referral code",
                dedupe_key=f"referral:referee:{referral_id}",
                notify_title="Your referral welcome bonus has landed",
            )
            return True
        except Exception as exc:
            # A referral payout must never take down the delivery that triggered it.
            print(f"Warning: could not settle referral for {referee_id}: {exc}")
            return False
