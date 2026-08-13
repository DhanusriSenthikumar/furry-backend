"""Outbound email with a console fallback.

If SMTP_HOST is blank the message is printed to the server log instead of being
sent, so password resets and order confirmations are fully demoable without a
mail server — the same graceful-degradation pattern the payment gateways use.
"""

import smtplib
from email.message import EmailMessage

from app.core.config import settings
from app.core.shipping import carrier_name


def _order_ref(order: dict) -> str:
    return str(order["_id"])[-8:]


def _order_link(order: dict) -> str:
    return f"{settings.frontend_url}/orders/{order['_id']}"


class EmailService:
    def send(self, to: str, subject: str, body: str) -> bool:
        """Returns True if the message was handed to an SMTP server, False if logged."""
        if not settings.email_configured:
            print(
                "\n--- EMAIL (not sent: SMTP_HOST is blank) ---\n"
                f"To: {to}\nSubject: {subject}\n\n{body}\n"
                "--- END EMAIL ---\n"
            )
            return False

        message = EmailMessage()
        message["From"] = settings.email_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)
        return True

    def send_password_reset(self, to: str, name: str, token: str) -> bool:
        link = f"{settings.frontend_url}/reset-password?token={token}"
        return self.send(
            to,
            "Reset your Furry Friends password",
            f"Hi {name},\n\n"
            f"Use the link below to choose a new password. It expires in "
            f"{settings.reset_token_expire_minutes} minutes.\n\n{link}\n\n"
            "If you didn't request this, you can safely ignore this email.",
        )

    def send_order_confirmation(self, to: str, name: str, order: dict) -> bool:
        lines = "\n".join(
            f"  {item['name']} x{item['quantity']} — ${item['price'] * item['quantity']:.2f}"
            for item in order.get("items", [])
        )
        return self.send(
            to,
            f"Your Furry Friends order #{_order_ref(order)}",
            f"Hi {name},\n\nThanks for your order! Here's what's on the way:\n\n{lines}\n\n"
            f"  Subtotal: ${order.get('subtotal', 0):.2f}\n"
            f"  Discount: -${order.get('discount', 0):.2f}\n"
            f"  Shipping: ${order.get('shipping_fee', 0):.2f}\n"
            f"  Tax: ${order.get('tax', 0):.2f}\n"
            f"  Total: ${order.get('total', 0):.2f}\n\n"
            f"Track it at {_order_link(order)}",
        )

    # ------------------------------------------------------------------ #
    # The rest of the lifecycle. Each one is triggered by an order changing
    # status, so a customer hears about the parcel without re-opening the site.
    # ------------------------------------------------------------------ #

    def send_order_shipped(self, to: str, name: str, order: dict) -> bool:
        shipment = order.get("shipment") or {}
        carrier = carrier_name(shipment.get("carrier", "")) if shipment.get("carrier") else ""
        tracking = shipment.get("tracking_number", "")
        url = shipment.get("tracking_url", "")

        if tracking:
            detail = f"\nCarrier: {carrier}\nTracking number: {tracking}\n"
            detail += f"Track the parcel: {url}\n" if url else "\n"
        else:
            detail = ""

        eta = shipment.get("estimated_delivery", "")
        if eta:
            detail += f"Estimated delivery: {eta}\n"

        return self.send(
            to,
            f"Your Furry Friends order #{_order_ref(order)} has shipped",
            f"Hi {name},\n\nGood news — your order is on its way.\n{detail}\n"
            f"Full order details: {_order_link(order)}",
        )

    def send_order_delivered(self, to: str, name: str, order: dict) -> bool:
        return self.send(
            to,
            f"Your Furry Friends order #{_order_ref(order)} was delivered",
            f"Hi {name},\n\nYour order has been marked delivered. We hope everyone in the "
            f"household approves.\n\n"
            f"Now that it's arrived you can review what you bought, and if something isn't "
            f"right you have {settings.return_window_days} days to start a return:\n\n"
            f"{_order_link(order)}",
        )

    def send_order_cancelled(self, to: str, name: str, order: dict, reason: str = "") -> bool:
        because = f"\nReason given: {reason}\n" if reason else ""
        return self.send(
            to,
            f"Your Furry Friends order #{_order_ref(order)} was cancelled",
            f"Hi {name},\n\nOrder #{_order_ref(order)} has been cancelled and the items "
            f"returned to stock.{because}\n"
            f"Any payment already taken is refunded to the original payment method.\n\n"
            f"{_order_link(order)}",
        )

    # ------------------------------------------------------------------ #
    # Stock alerts
    # ------------------------------------------------------------------ #

    def send_back_in_stock(self, to: str, name: str, product: dict) -> bool:
        link = f"{settings.frontend_url}/products/{product['slug']}"
        stock = product.get("stock", 0)
        # Whoever asked first is competing with everyone else who asked, so say
        # plainly how thin the shelf is rather than implying it will keep.
        hurry = f"There {'is' if stock == 1 else 'are'} {stock} in stock.\n" if 0 < stock <= 5 else ""
        return self.send(
            to,
            f"{product['name']} is back in stock",
            f"Hi {name},\n\nYou asked us to let you know when {product['name']} came "
            f"back — it has.\n{hurry}\n{link}\n\n"
            "This was a one-off alert, so you won't hear from us about it again "
            "unless you ask.",
        )

    # ------------------------------------------------------------------ #
    # Returns
    # ------------------------------------------------------------------ #

    def send_return_requested(self, to: str, name: str, ret: dict) -> bool:
        lines = "\n".join(f"  {item['name']} x{item['quantity']}" for item in ret.get("items", []))
        return self.send(
            to,
            f"We got your return request #{str(ret['_id'])[-8:]}",
            f"Hi {name},\n\nWe've received your request to return:\n\n{lines}\n\n"
            f"Our team will review it and email you the decision. Estimated refund if "
            f"approved: ${ret.get('refund_estimate', 0):.2f}.\n\n"
            f"{settings.frontend_url}/returns",
        )

    def send_return_decision(self, to: str, name: str, ret: dict) -> bool:
        approved = ret.get("status") == "approved"
        note = ret.get("resolution_note", "")
        body = (
            f"Hi {name},\n\nYour return #{str(ret['_id'])[-8:]} has been approved. "
            f"Send the items back and we'll refund ${ret.get('refund_estimate', 0):.2f} "
            f"once they arrive.\n"
            if approved
            else f"Hi {name},\n\nWe weren't able to approve return #{str(ret['_id'])[-8:]}.\n"
        )
        if note:
            body += f"\nNote from our team: {note}\n"
        return self.send(
            to,
            f"Your return #{str(ret['_id'])[-8:]} was {'approved' if approved else 'declined'}",
            f"{body}\n{settings.frontend_url}/returns",
        )

    def send_refund_issued(self, to: str, name: str, ret: dict) -> bool:
        method = ret.get("refund_method", "")
        settled = (
            f"It's been sent back to your {method} payment.\n"
            if method and method != "manual"
            else "Our team will settle it with you directly.\n"
        )
        return self.send(
            to,
            f"Refund of ${ret.get('refund_amount', 0):.2f} for return #{str(ret['_id'])[-8:]}",
            f"Hi {name},\n\nWe've refunded ${ret.get('refund_amount', 0):.2f} for your "
            f"return.\n{settled}\nDepending on your bank it can take a few business days "
            f"to appear.\n\n{settings.frontend_url}/returns",
        )

    # ------------------------------------------------------------------ #
    # Subscriptions
    # ------------------------------------------------------------------ #

    def send_subscription_order(self, to: str, name: str, order: dict, subscription: dict) -> bool:
        """A repeat delivery has been raised and is waiting to be paid.

        Said plainly because nothing was charged automatically — the store keeps
        no card on file, and a customer who thinks a subscription bills itself
        would find out when the parcel didn't arrive.
        """
        item = (order.get("items") or [{}])[0]
        every = subscription.get("interval_days", 30)
        return self.send(
            to,
            f"Your repeat delivery is ready — order #{_order_ref(order)}",
            f"Hi {name},\n\nYour subscription for {item.get('name', 'your item')} "
            f"(x{item.get('quantity', 1)}, every {every} days) is due, so we've put the "
            f"order together with your subscriber discount already applied:\n\n"
            f"  Subtotal: ${order.get('subtotal', 0):.2f}\n"
            f"  Subscriber discount: -${order.get('discount', 0):.2f}\n"
            f"  Shipping: ${order.get('shipping_fee', 0):.2f}\n"
            f"  Tax: ${order.get('tax', 0):.2f}\n"
            f"  Total: ${order.get('total', 0):.2f}\n\n"
            f"It's waiting for payment — nothing has been charged:\n{_order_link(order)}\n\n"
            f"Pause, reschedule or cancel any time at "
            f"{settings.frontend_url}/subscriptions",
        )


email_service = EmailService()
