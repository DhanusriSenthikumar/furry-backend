from pydantic import BaseModel, Field

from app.core.enums import LoyaltyKind, LoyaltyTier


class LoyaltyEntryOut(BaseModel):
    """One movement of the balance. Positive points came in, negative went out."""

    id: str
    points: int
    kind: LoyaltyKind
    reason: str
    order_id: str | None = None
    created_at: str


class TierStandingOut(BaseModel):
    tier: LoyaltyTier
    label: str
    multiplier: float
    lifetime_points: int
    next_tier: LoyaltyTier | None = None
    next_tier_label: str | None = None
    points_to_next_tier: int
    progress_percent: float


class LoyaltySummaryOut(BaseModel):
    """Everything the rewards page draws, in one call."""

    enabled: bool
    balance: int
    # What the balance is worth today, at the current redemption rate.
    balance_value: float
    standing: TierStandingOut
    # Rate card, served from the backend so the marketing copy on the storefront
    # can never quote a rate the checkout doesn't honour.
    points_per_currency: float
    points_per_redeemed_currency: float
    min_redemption: int
    max_redemption_percent: float
    recent: list[LoyaltyEntryOut]


class RedemptionPreviewOut(BaseModel):
    """What a basket of this size allows, so checkout can bound its input before
    the customer types a number that would be rejected."""

    balance: int
    max_points: int
    max_value: float
    min_redemption: int
    eligible: bool
    # Why not, when `eligible` is false — a balance too small to spend and a
    # basket too small to spend it on need different wording.
    reason: str = ""


class LoyaltyAdjust(BaseModel):
    """Staff moving a balance by hand — goodwill, or correcting a mistake."""

    points: int = Field(description="Positive to credit, negative to debit")
    reason: str = Field(min_length=1, max_length=200)
