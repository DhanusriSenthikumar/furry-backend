import stripe

from app.core.config import settings
from app.core.exceptions import PaymentGatewayNotConfiguredError, ValidationError


class StripeClient:
    def _require_configured(self) -> None:
        if not settings.stripe_secret_key:
            raise PaymentGatewayNotConfiguredError("Stripe is not configured (STRIPE_SECRET_KEY is empty)")
        stripe.api_key = settings.stripe_secret_key

    def create_payment_intent(self, amount: float, currency: str, order_id: str) -> dict:
        self._require_configured()
        intent = stripe.PaymentIntent.create(
            amount=round(amount * 100),
            currency=currency,
            metadata={"order_id": order_id},
        )
        return {"id": intent["id"], "client_secret": intent["client_secret"]}

    def refund(self, payment_intent_id: str, amount: float) -> dict:
        """Send money back against a settled intent. Partial refunds are the
        norm here — a customer usually returns some of an order, not all of it."""
        self._require_configured()
        refund = stripe.Refund.create(
            payment_intent=payment_intent_id,
            amount=round(amount * 100),
        )
        return {"id": refund["id"], "status": refund["status"]}

    def construct_webhook_event(self, payload: bytes, sig_header: str) -> dict:
        self._require_configured()
        if not settings.stripe_webhook_secret:
            raise PaymentGatewayNotConfiguredError("Stripe webhook secret is not configured")
        try:
            return stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
        except (ValueError, stripe.error.SignatureVerificationError) as exc:
            raise ValidationError(f"Invalid Stripe webhook payload: {exc}")


stripe_client = StripeClient()
