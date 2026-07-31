from datetime import datetime, timezone

from app.core.exceptions import NotFoundError, ValidationError
from app.modules.orders.service import OrderService
from app.modules.payments.razorpay_client import RazorpayClient
from app.modules.payments.repository import PaymentRepository
from app.modules.payments.schemas import VerifyRazorpayPayment
from app.modules.payments.stripe_client import StripeClient

STRIPE_CURRENCY = "usd"
RAZORPAY_CURRENCY = "INR"


class PaymentService:
    def __init__(
        self,
        repo: PaymentRepository,
        orders: OrderService,
        stripe_client: StripeClient,
        razorpay_client: RazorpayClient,
    ):
        self.repo = repo
        self.orders = orders
        self.stripe_client = stripe_client
        self.razorpay_client = razorpay_client

    async def _order_pending_for_user(self, user: dict, order_id: str) -> dict:
        order = await self.orders.get_owned(order_id, user)
        if order["status"] != "pending_payment":
            raise ValidationError("Order is not awaiting payment")
        return order

    async def _record_payment(self, order_id: str, user_id: str, gateway: str, reference: str, amount: float, currency: str) -> dict:
        doc = {
            "order_id": order_id,
            "user_id": user_id,
            "gateway": gateway,
            "gateway_reference": reference,
            "amount": amount,
            "currency": currency,
            "status": "created",
            "created_at": datetime.now(timezone.utc),
        }
        return await self.repo.insert(doc)

    async def create_stripe_intent(self, user: dict, order_id: str) -> dict:
        order = await self._order_pending_for_user(user, order_id)
        intent = self.stripe_client.create_payment_intent(order["total"], STRIPE_CURRENCY, order_id)
        payment = await self._record_payment(
            order_id, str(user["_id"]), "stripe", intent["id"], order["total"], STRIPE_CURRENCY
        )
        return {"payment_id": str(payment["_id"]), "client_secret": intent["client_secret"]}

    async def handle_stripe_webhook(self, payload: bytes, sig_header: str) -> None:
        event = self.stripe_client.construct_webhook_event(payload, sig_header)
        if event["type"] == "payment_intent.succeeded":
            intent = event["data"]["object"]
            await self._settle_by_reference("stripe", intent["id"])

    async def create_razorpay_order(self, user: dict, order_id: str) -> dict:
        order = await self._order_pending_for_user(user, order_id)
        razorpay_order = self.razorpay_client.create_order(order["total"], RAZORPAY_CURRENCY, order_id)
        payment = await self._record_payment(
            order_id, str(user["_id"]), "razorpay", razorpay_order["id"], order["total"], RAZORPAY_CURRENCY
        )
        return {
            "payment_id": str(payment["_id"]),
            "razorpay_order_id": razorpay_order["id"],
            "amount": razorpay_order["amount"],
            "currency": razorpay_order["currency"],
        }

    async def verify_razorpay_payment(self, user: dict, payload: VerifyRazorpayPayment) -> dict:
        await self._order_pending_for_user(user, payload.order_id)
        verified = self.razorpay_client.verify_payment_signature(
            {
                "razorpay_order_id": payload.razorpay_order_id,
                "razorpay_payment_id": payload.razorpay_payment_id,
                "razorpay_signature": payload.razorpay_signature,
            }
        )
        if not verified:
            raise ValidationError("Razorpay signature verification failed")
        # Razorpay refunds are issued against the payment id, not the order id we
        # keyed the record on — store it now, while we have it.
        return await self._settle_by_reference(
            "razorpay", payload.razorpay_order_id, gateway_payment_id=payload.razorpay_payment_id
        )

    async def confirm_cod(self, user: dict, order_id: str) -> dict:
        order = await self._order_pending_for_user(user, order_id)
        await self._record_payment(order_id, str(user["_id"]), "cod", order_id, order["total"], "USD")
        await self.orders.set_status(order_id, "processing", "Cash on Delivery confirmed")
        return await self.orders.get_owned(order_id, user)

    async def _settle_by_reference(self, gateway: str, reference: str, gateway_payment_id: str = "") -> dict:
        payment = await self.repo.find_by_reference(gateway, reference)
        if not payment:
            raise NotFoundError("Payment record not found")
        update = {"status": "succeeded"}
        if gateway_payment_id:
            update["gateway_payment_id"] = gateway_payment_id
        await self.repo.update_by_id(str(payment["_id"]), update)
        await self.orders.mark_paid(payment["order_id"], note=f"Paid via {gateway}")
        return payment
