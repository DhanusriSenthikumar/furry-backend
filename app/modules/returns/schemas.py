from pydantic import BaseModel, Field

from app.core.enums import ReturnReason, ReturnStatus


class ReturnItemIn(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    reason: ReturnReason


class ReturnCreate(BaseModel):
    order_id: str
    items: list[ReturnItemIn] = Field(min_length=1)
    comment: str = Field(default="", max_length=1000)


class ReturnResolve(BaseModel):
    """Staff ruling on a request. The note is shown to the customer verbatim."""

    note: str = Field(default="", max_length=500)


class ReturnRefund(BaseModel):
    note: str = Field(default="", max_length=500)
    # Damaged goods don't go back on the shelf. Staff decide per return, because
    # only they have seen what came back.
    restock: bool = True
    # Overrides the computed refund. Left null the customer gets exactly what the
    # pricing rules say they're owed.
    amount: float | None = Field(default=None, ge=0)


class ReturnItemOut(BaseModel):
    product_id: str
    name: str
    price: float
    quantity: int
    reason: ReturnReason


class RefundBreakdownOut(BaseModel):
    goods: float
    discount_share: float
    tax_share: float
    shipping_refund: float
    total: float


class ReturnOut(BaseModel):
    id: str
    user_id: str
    order_id: str
    # Carried so the customer's returns list and the admin queue can label a row
    # without fetching the order for each one.
    order_ref: str
    items: list[ReturnItemOut]
    comment: str
    status: ReturnStatus
    # What the customer is owed if this is approved and settled as-is.
    refund_estimate: float
    refund_breakdown: RefundBreakdownOut
    # What was actually paid back. 0 until the return reaches "refunded".
    refund_amount: float = 0.0
    refund_method: str = ""
    refund_reference: str = ""
    restocked: bool = False
    resolution_note: str = ""
    resolved_by: str = ""
    created_at: str
    updated_at: str


class ReturnableItemOut(BaseModel):
    """One order line with how much of it is still eligible to send back."""

    product_id: str
    name: str
    price: float
    quantity_ordered: int
    quantity_returnable: int


class ReturnEligibilityOut(BaseModel):
    order_id: str
    can_return: bool
    reason: str = ""
    return_window_ends: str | None = None
    items: list[ReturnableItemOut]
