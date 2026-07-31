import razorpay

from app.core.config import settings
from app.core.exceptions import PaymentGatewayNotConfiguredError, ValidationError


class RazorpayClient:
    def _client(self) -> razorpay.Client:
        if not settings.razorpay_key_id or not settings.razorpay_key_secret:
            raise PaymentGatewayNotConfiguredError(
                "Razorpay is not configured (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are empty)"
            )
        return razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))

    def create_order(self, amount: float, currency: str, order_id: str) -> dict:
        client = self._client()
        razorpay_order = client.order.create(
            {
                "amount": round(amount * 100),
                "currency": currency,
                "notes": {"order_id": order_id},
            }
        )
        return razorpay_order

    def refund(self, payment_id: str, amount: float) -> dict:
        """Razorpay refunds are keyed on the *payment* id, not the order id."""
        client = self._client()
        refund = client.payment.refund(payment_id, {"amount": round(amount * 100)})
        return {"id": refund["id"], "status": refund.get("status", "processed")}

    def verify_payment_signature(self, params: dict) -> bool:
        client = self._client()
        try:
            client.utility.verify_payment_signature(params)
            return True
        except razorpay.errors.SignatureVerificationError:
            return False
        except Exception as exc:
            raise ValidationError(f"Could not verify Razorpay payment: {exc}")


razorpay_client = RazorpayClient()
