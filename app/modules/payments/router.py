from fastapi import APIRouter, Depends, Header, Request

from app.core.config import settings
from app.deps import get_current_user, get_db
from app.modules.orders.repository import OrderRepository
from app.modules.orders.router import order_out
from app.modules.orders.service import OrderService
from app.modules.payments.razorpay_client import razorpay_client
from app.modules.payments.repository import PaymentRepository
from app.modules.payments.schemas import (
    OrderIdIn,
    RazorpayOrderOut,
    StripeIntentOut,
    VerifyRazorpayPayment,
)
from app.modules.payments.service import PaymentService
from app.modules.payments.stripe_client import stripe_client
from app.modules.products.repository import ProductRepository
from app.modules.users.repository import UserRepository

router = APIRouter(prefix="/payments", tags=["payments"])


def _service(db=Depends(get_db)) -> PaymentService:
    order_service = OrderService(
        OrderRepository(db), ProductRepository(db), users=UserRepository(db)
    )
    return PaymentService(PaymentRepository(db), order_service, stripe_client, razorpay_client)


@router.post("/stripe/create-intent", response_model=StripeIntentOut)
async def create_stripe_intent(
    payload: OrderIdIn, user: dict = Depends(get_current_user), service: PaymentService = Depends(_service)
):
    result = await service.create_stripe_intent(user, payload.order_id)
    return StripeIntentOut(**result)


@router.post("/stripe/webhook")
async def stripe_webhook(
    request: Request, stripe_signature: str = Header(default=""), service: PaymentService = Depends(_service)
):
    payload = await request.body()
    await service.handle_stripe_webhook(payload, stripe_signature)
    return {"ok": True}


@router.post("/razorpay/create-order", response_model=RazorpayOrderOut)
async def create_razorpay_order(
    payload: OrderIdIn, user: dict = Depends(get_current_user), service: PaymentService = Depends(_service)
):
    result = await service.create_razorpay_order(user, payload.order_id)
    return RazorpayOrderOut(**result, key_id=settings.razorpay_key_id)


@router.post("/razorpay/verify")
async def verify_razorpay_payment(
    payload: VerifyRazorpayPayment, user: dict = Depends(get_current_user), service: PaymentService = Depends(_service)
):
    await service.verify_razorpay_payment(user, payload)
    return {"ok": True}


@router.post("/cod/confirm")
async def confirm_cod(
    payload: OrderIdIn, user: dict = Depends(get_current_user), service: PaymentService = Depends(_service)
):
    order = await service.confirm_cod(user, payload.order_id)
    return order_out(order)
