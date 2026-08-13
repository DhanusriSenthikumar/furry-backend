from pydantic import BaseModel, Field

from app.core.enums import Carrier, OrderStatus


class ShippingAddress(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    line1: str = Field(min_length=1, max_length=200)
    line2: str = ""
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    zip: str = Field(min_length=1, max_length=20)
    phone: str = Field(min_length=1, max_length=20)


class OrderItemIn(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)


class OrderCreate(BaseModel):
    items: list[OrderItemIn] = Field(min_length=1)
    shipping_address: ShippingAddress
    coupon_code: str | None = None
    # Loyalty points to put towards this order. Clamped server-side to what the
    # customer holds and what the basket allows, never taken on trust.
    redeem_points: int = Field(default=0, ge=0)


class OrderQuote(BaseModel):
    """Price a basket without placing an order — powers the checkout summary."""

    items: list[OrderItemIn] = Field(min_length=1)
    coupon_code: str | None = None
    redeem_points: int = Field(default=0, ge=0)


class OrderItemOut(BaseModel):
    product_id: str
    name: str
    price: float
    quantity: int


class StatusHistoryEntry(BaseModel):
    status: OrderStatus
    note: str = ""
    at: str


class ShipmentCreate(BaseModel):
    """Admin hands a parcel to a carrier. Recording this also ships the order —
    an order can't be in transit without something to trace it by."""

    carrier: Carrier
    tracking_number: str = Field(min_length=1, max_length=64)
    # Free text rather than a date so a courier's own wording ("Tue 4 Aug",
    # "2–3 business days") can be passed through untouched.
    estimated_delivery: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=300)


class ShipmentOut(BaseModel):
    carrier: Carrier
    carrier_name: str
    tracking_number: str
    # Built server-side from the carrier so every surface links identically.
    # Empty for a courier with no public tracking page.
    tracking_url: str
    estimated_delivery: str = ""
    shipped_at: str


class OrderTotalsOut(BaseModel):
    subtotal: float
    discount: float
    shipping_fee: float
    tax: float
    # Loyalty points spent, in money. Applied after tax and shipping because
    # points are tender rather than a price cut.
    rewards_discount: float = 0.0
    total: float


class OrderQuoteOut(OrderTotalsOut):
    items: list[OrderItemOut]
    coupon_code: str | None = None
    # Set when a code was supplied but couldn't be applied, so the UI can explain
    # why without failing the whole quote.
    coupon_error: str | None = None
    # Points the server would actually take — the request is clamped, so this can
    # be lower than what was asked for.
    redeem_points: int = 0
    free_shipping_threshold: float
    amount_to_free_shipping: float


class OrderOut(BaseModel):
    id: str
    user_id: str
    items: list[OrderItemOut]
    shipping_address: ShippingAddress
    subtotal: float
    discount: float
    coupon_code: str | None = None
    shipping_fee: float
    tax: float
    rewards_discount: float = 0.0
    # Points actually spent on this order. Returned to the customer if it is
    # cancelled or refunded.
    redeem_points: int = 0
    total: float
    status: OrderStatus
    status_history: list[StatusHistoryEntry]
    # "subscription" for a repeat delivery, empty for a normal basket.
    source: str = ""
    subscription_id: str | None = None
    can_cancel: bool = False
    # Set once the parcel is handed over; null before that and on cancelled orders.
    shipment: ShipmentOut | None = None
    # Money already given back across every settled return on this order.
    refunded_amount: float = 0.0
    can_return: bool = False
    # Why not, when can_return is false and the order was actually delivered —
    # so the UI can say "the 30-day window closed" instead of hiding the button.
    return_blocked_reason: str = ""
    created_at: str


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    note: str = ""


class OrderCancel(BaseModel):
    reason: str = Field(default="", max_length=300)
