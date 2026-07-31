from pydantic import BaseModel

from app.core.enums import PaymentGateway, PaymentStatus


class OrderIdIn(BaseModel):
    order_id: str


class StripeIntentOut(BaseModel):
    payment_id: str
    client_secret: str


class RazorpayOrderOut(BaseModel):
    payment_id: str
    razorpay_order_id: str
    amount: int
    currency: str
    key_id: str


class VerifyRazorpayPayment(BaseModel):
    order_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class PaymentOut(BaseModel):
    id: str
    order_id: str
    user_id: str
    gateway: PaymentGateway
    status: PaymentStatus
    amount: float
    currency: str
    created_at: str
