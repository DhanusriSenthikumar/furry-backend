from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.enums import SubscriptionStatus
from app.modules.orders.schemas import ShippingAddress

# Presets the UI offers. Any interval inside the configured bounds is accepted —
# these are just the ones worth a single click.
INTERVAL_PRESETS = [
    {"days": 14, "label": "Every 2 weeks"},
    {"days": 30, "label": "Every month"},
    {"days": 60, "label": "Every 2 months"},
    {"days": 90, "label": "Every 3 months"},
]


class SubscriptionCreate(BaseModel):
    product_id: str
    quantity: int = Field(default=1, gt=0, le=99)
    interval_days: int = Field(
        ge=settings.subscription_min_interval_days,
        le=settings.subscription_max_interval_days,
    )
    shipping_address: ShippingAddress
    # Leave unset to take the first delivery one interval from now. Set it to
    # ship the first one straight away.
    start_now: bool = False


class SubscriptionUpdate(BaseModel):
    quantity: int | None = Field(default=None, gt=0, le=99)
    interval_days: int | None = Field(
        default=None,
        ge=settings.subscription_min_interval_days,
        le=settings.subscription_max_interval_days,
    )
    shipping_address: ShippingAddress | None = None


class SubscriptionOut(BaseModel):
    id: str
    user_id: str
    product_id: str
    product_name: str
    product_slug: str
    product_image: str = ""
    # Today's catalogue price, so a plan that has been running for months shows
    # what the next delivery will actually cost rather than what the first did.
    unit_price: float
    quantity: int
    interval_days: int
    discount_percent: float
    # What one delivery comes to, after the subscription discount.
    estimated_total: float
    status: SubscriptionStatus
    next_delivery_at: str | None = None
    shipping_address: ShippingAddress
    orders_placed: int = 0
    last_order_id: str | None = None
    last_ordered_at: str | None = None
    # Set after a run couldn't place the order — out of stock, product withdrawn.
    # Cleared by the next successful delivery.
    last_error: str = ""
    in_stock: bool = True
    created_at: str


class SubscriptionOfferOut(BaseModel):
    """What the product page needs to draw the Subscribe & Save box, answered
    for signed-out visitors too so the saving is visible before signing up."""

    enabled: bool
    product_id: str
    discount_percent: float
    unit_price: float
    subscription_price: float
    saving_per_delivery: float
    min_interval_days: int
    max_interval_days: int
    intervals: list[dict]
    # The customer's existing plan for this product, if they have one.
    subscribed: bool = False
    subscription_id: str | None = None


class SubscriptionRunOut(BaseModel):
    """Outcome of a delivery run, for the admin screen and for whatever cron
    calls it."""

    claimed: int
    ordered: int
    skipped: int
    failed: int
    paused: int
    released_stale: int
    details: list[str]
