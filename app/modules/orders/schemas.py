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


class OrderQuote(BaseModel):
    """Price a basket without placing an order — powers the checkout summary."""

    items: list[OrderItemIn] = Field(min_length=1)
    coupon_code: str | None = None


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
    total: float


class OrderQuoteOut(OrderTotalsOut):
    items: list[OrderItemOut]
    coupon_code: str | None = None
    # Set when a code was supplied but couldn't be applied, so the UI can explain
    # why without failing the whole quote.
    coupon_error: str | None = None
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
    total: float
    status: OrderStatus
    status_history: list[StatusHistoryEntry]
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
