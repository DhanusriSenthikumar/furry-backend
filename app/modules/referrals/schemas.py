from pydantic import BaseModel

from app.core.enums import ReferralStatus


class ReferralInviteOut(BaseModel):
    """One person you invited. The name is trimmed to a first name and the email
    is masked — a referrer is owed progress, not a copy of someone's contact
    details."""

    id: str
    referee_name: str
    referee_email: str
    status: ReferralStatus
    points_earned: int
    created_at: str
    rewarded_at: str | None = None


class ReferralSummaryOut(BaseModel):
    enabled: bool
    code: str
    # Ready to paste. Built server-side from FRONTEND_URL so it is right in
    # every environment without the browser guessing its own origin.
    share_url: str
    referrer_points: int
    referee_points: int
    invited: int
    pending: int
    rewarded: int
    points_earned: int
    invites: list[ReferralInviteOut]


class ReferralCodeCheckOut(BaseModel):
    """Signup validating a code before it commits the account, so a typo is
    caught while it can still be fixed."""

    code: str
    valid: bool
    # Whose code it is, first name only, so the newcomer can confirm they got it
    # from the right person.
    referrer_name: str = ""
    referee_points: int = 0
    reason: str = ""
