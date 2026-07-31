import asyncio
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.email import email_service
from app.core.exceptions import (
    ForbiddenError,
    InsufficientStockError,
    NotFoundError,
    ValidationError,
)
from app.core.pagination import Pagination
from app.core.pricing import price_order
from app.core.shipping import carrier_name, tracking_url
from app.modules.coupons.service import CouponService
from app.modules.orders.repository import OrderRepository
from app.modules.orders.schemas import OrderCreate, OrderQuote, OrderStatusUpdate, ShipmentCreate
from app.modules.products.repository import ProductRepository
from app.modules.stock_alerts.service import StockAlertService
from app.modules.users.repository import UserRepository

# Statuses whose stock has been reserved but not yet shipped — cancelling one of
# these must put the units back on the shelf.
RESTOCKABLE_STATUSES = {"pending_payment", "paid", "processing"}

# A customer can pull the plug up until the parcel leaves the warehouse.
CUSTOMER_CANCELLABLE_STATUSES = {"pending_payment", "paid", "processing"}

# Statuses worth emailing about on arrival. Everything else is either noise
# (processing) or already covered by its own message (the confirmation at
# checkout, the refund notice a return sends).
NOTIFIED_STATUSES = {"shipped", "delivered", "cancelled"}


class OrderService:
    def __init__(
        self,
        repo: OrderRepository,
        products: ProductRepository,
        coupons: CouponService | None = None,
        users: UserRepository | None = None,
        alerts: StockAlertService | None = None,
    ):
        self.repo = repo
        self.products = products
        self.coupons = coupons
        # Only needed to address lifecycle emails. Without it the order still
        # moves; the customer just doesn't hear about it.
        self.users = users
        # Cancelling puts units back on the shelf, which can be what someone
        # else has been waiting for.
        self.alerts = alerts

    @staticmethod
    def can_cancel(order: dict) -> bool:
        return order.get("status") in CUSTOMER_CANCELLABLE_STATUSES

    @staticmethod
    def delivered_at(order: dict) -> datetime | None:
        """When the parcel was marked delivered, from the status timeline."""
        for entry in reversed(order.get("status_history", [])):
            if entry.get("status") == "delivered":
                at = entry.get("at")
                if isinstance(at, datetime):
                    # Mongo hands back naive UTC; normalize before any comparison.
                    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
                return None
        return None

    @classmethod
    def return_window_ends(cls, order: dict) -> datetime | None:
        delivered = cls.delivered_at(order)
        return delivered + timedelta(days=settings.return_window_days) if delivered else None

    @classmethod
    def return_eligibility(cls, order: dict) -> tuple[bool, str]:
        """(can_return, reason it can't). The returns service re-checks this and
        also verifies quantities — this is the cheap version the order page uses
        to decide whether to offer the button."""
        status = order.get("status")
        if status == "refunded":
            return False, "This order has already been fully refunded"
        if status != "delivered":
            return False, "A return can be started once the order has been delivered"

        ends = cls.return_window_ends(order)
        if ends is None:
            # Delivered with no timestamp — a hand-edited or migrated order.
            # Allow it rather than trapping the customer on a technicality.
            return True, ""
        if datetime.now(timezone.utc) > ends:
            return False, f"The {settings.return_window_days}-day return window closed on {ends.date().isoformat()}"
        return True, ""

    async def _price_items(self, items) -> tuple[list[dict], float]:
        """Resolves order lines against live product data. Does not touch stock."""
        order_items: list[dict] = []
        subtotal = 0.0
        for item in items:
            product = await self.products.find_by_id(item.product_id)
            if not product:
                raise NotFoundError(f"Product not found: {item.product_id}")
            subtotal += product["price"] * item.quantity
            order_items.append(
                {
                    "product_id": item.product_id,
                    "name": product["name"],
                    "price": product["price"],
                    "quantity": item.quantity,
                }
            )
        return order_items, round(subtotal, 2)

    async def _resolve_coupon(self, code: str | None, subtotal: float, user_id: str) -> tuple[dict | None, float]:
        if not code or not self.coupons:
            return None, 0.0
        return await self.coupons.validate(code, subtotal, user_id)

    async def quote(self, user: dict, payload: OrderQuote) -> dict:
        """Totals for a basket, with a soft failure if the coupon doesn't apply."""
        order_items, subtotal = await self._price_items(payload.items)

        coupon_code: str | None = None
        coupon_error: str | None = None
        discount = 0.0
        if payload.coupon_code:
            try:
                coupon, discount = await self._resolve_coupon(payload.coupon_code, subtotal, str(user["_id"]))
                coupon_code = coupon["code"] if coupon else None
            except ValidationError as exc:
                coupon_error = exc.message

        totals = price_order(subtotal, discount)
        remaining = max(settings.free_shipping_threshold - (totals.subtotal - totals.discount), 0.0)
        return {
            **totals.as_dict(),
            "items": order_items,
            "coupon_code": coupon_code,
            "coupon_error": coupon_error,
            "free_shipping_threshold": settings.free_shipping_threshold,
            "amount_to_free_shipping": round(remaining, 2),
        }

    async def checkout(self, user: dict, payload: OrderCreate) -> dict:
        order_items, subtotal = await self._price_items(payload.items)

        # Validate the coupon before reserving stock, so a rejected code never
        # leaves inventory decremented behind an order that was not created.
        coupon, discount = await self._resolve_coupon(payload.coupon_code, subtotal, str(user["_id"]))
        totals = price_order(subtotal, discount)

        decremented: list[tuple[str, int]] = []
        try:
            for item in payload.items:
                ok = await self.products.try_decrement_stock(item.product_id, item.quantity)
                if not ok:
                    name = next(i["name"] for i in order_items if i["product_id"] == item.product_id)
                    raise InsufficientStockError(f"Not enough stock for {name}")
                decremented.append((item.product_id, item.quantity))
        except Exception:
            for product_id, qty in decremented:
                await self.products.restore_stock(product_id, qty)
            raise

        now = datetime.now(timezone.utc)
        doc = {
            "user_id": str(user["_id"]),
            "items": order_items,
            "shipping_address": payload.shipping_address.model_dump(),
            **totals.as_dict(),
            "coupon_code": coupon["code"] if coupon else None,
            "status": "pending_payment",
            "status_history": [{"status": "pending_payment", "note": "Order placed", "at": now}],
            "created_at": now,
        }
        order = await self.repo.insert(doc)

        if coupon and self.coupons:
            await self.coupons.mark_redeemed(str(coupon["_id"]))

        await self._notify(email_service.send_order_confirmation, user["email"], user["name"], order)
        return order

    @staticmethod
    async def _notify(fn, *args) -> None:
        """Email must never take an order down — SMTP runs off the event loop."""
        try:
            await asyncio.to_thread(fn, *args)
        except Exception as exc:
            print(f"Warning: could not send email: {exc}")

    async def list_mine(self, user_id: str) -> list[dict]:
        return await self.repo.find_by_user(user_id)

    async def get_owned(self, order_id: str, user: dict) -> dict:
        order = await self.repo.find_by_id(order_id)
        if not order:
            raise NotFoundError("Order not found")
        if order["user_id"] != str(user["_id"]) and not user.get("is_admin"):
            raise ForbiddenError("Not allowed to view this order")
        return order

    async def list_all(self, pagination: Pagination, status: str | None = None) -> list[dict]:
        return await self.repo.find_all(skip=pagination.skip, limit=pagination.page_size, status=status)

    async def set_status(self, order_id: str, new_status: str, note: str = "", extra: dict | None = None) -> dict:
        order = await self.repo.find_by_id(order_id)
        if not order:
            raise NotFoundError("Order not found")

        if new_status == "cancelled" and order["status"] in RESTOCKABLE_STATUSES:
            for item in order["items"]:
                await self.products.restore_stock(item["product_id"], item["quantity"])
                if self.alerts is not None:
                    await self.alerts.flush(item["product_id"])

        now = datetime.now(timezone.utc)
        history = order.get("status_history", [])
        history.append({"status": new_status, "note": note, "at": now})

        update = {"status": new_status, "status_history": history, **(extra or {})}
        updated = await self.repo.update_by_id(order_id, update)

        # Only on a real transition — re-saving the same status shouldn't email
        # the customer twice.
        if new_status in NOTIFIED_STATUSES and order.get("status") != new_status:
            await self._notify_status(updated, new_status, note)
        return updated

    async def _customer(self, order: dict) -> dict | None:
        if self.users is None:
            return None
        return await self.users.find_by_id(order["user_id"])

    async def _notify_status(self, order: dict, status: str, note: str) -> None:
        customer = await self._customer(order)
        if not customer:
            return

        to, name = customer["email"], customer.get("name", "there")
        if status == "shipped":
            await self._notify(email_service.send_order_shipped, to, name, order)
        elif status == "delivered":
            await self._notify(email_service.send_order_delivered, to, name, order)
        elif status == "cancelled":
            await self._notify(email_service.send_order_cancelled, to, name, order, note)

    async def ship(self, order_id: str, payload: ShipmentCreate) -> dict:
        """Record the carrier and tracking number, and move the order to shipped.

        Refused before payment: a parcel shouldn't leave on an order nobody has
        paid for, and shipping it would strand the customer's cancel button.
        """
        order = await self.repo.find_by_id(order_id)
        if not order:
            raise NotFoundError("Order not found")
        if order["status"] in ("cancelled", "refunded"):
            raise ValidationError(f"A {order['status']} order can't be shipped")
        if order["status"] == "pending_payment":
            raise ValidationError("This order hasn't been paid for yet")

        tracking_number = payload.tracking_number.strip()
        shipment = {
            "carrier": payload.carrier,
            "tracking_number": tracking_number,
            "tracking_url": tracking_url(payload.carrier, tracking_number),
            "estimated_delivery": payload.estimated_delivery.strip(),
            "shipped_at": datetime.now(timezone.utc),
        }
        note = payload.note.strip() or f"Handed to {carrier_name(payload.carrier)} — {tracking_number}"

        if order["status"] == "shipped":
            # Correcting a mistyped number, not a new despatch: update the
            # shipment and log it without re-sending the "it's on its way" email.
            history = order.get("status_history", [])
            history.append({"status": "shipped", "note": note, "at": datetime.now(timezone.utc)})
            return await self.repo.update_by_id(
                order_id, {"shipment": shipment, "status_history": history}
            )

        return await self.set_status(order_id, "shipped", note, extra={"shipment": shipment})

    async def record_refund(self, order_id: str, amount: float, note: str) -> dict:
        """Add `amount` to what this order has given back, and mark the whole
        order refunded once nothing is left to refund."""
        order = await self.repo.find_by_id(order_id)
        if not order:
            raise NotFoundError("Order not found")

        refunded = round((order.get("refunded_amount", 0.0) or 0.0) + amount, 2)
        # Float arithmetic across several partial refunds can land a cent short
        # of the total; treat that as fully refunded rather than leaving the
        # order stuck one penny away.
        fully_refunded = refunded >= round(order.get("total", 0.0), 2) - 0.01

        if fully_refunded and order["status"] != "refunded":
            return await self.set_status(order_id, "refunded", note, extra={"refunded_amount": refunded})

        history = order.get("status_history", [])
        history.append({"status": order["status"], "note": note, "at": datetime.now(timezone.utc)})
        return await self.repo.update_by_id(
            order_id, {"refunded_amount": refunded, "status_history": history}
        )

    async def update_status_admin(self, order_id: str, payload: OrderStatusUpdate) -> dict:
        return await self.set_status(order_id, payload.status, payload.note)

    async def cancel_own(self, order_id: str, user: dict, reason: str = "") -> dict:
        order = await self.get_owned(order_id, user)
        if order["status"] == "cancelled":
            raise ValidationError("This order is already cancelled")
        if not self.can_cancel(order):
            raise ValidationError(f"An order that is already {order['status'].replace('_', ' ')} can't be cancelled")

        note = f"Cancelled by customer{f': {reason}' if reason else ''}"
        return await self.set_status(order_id, "cancelled", note)

    async def mark_paid(self, order_id: str, note: str = "Payment confirmed") -> dict:
        order = await self.repo.find_by_id(order_id)
        if not order:
            raise NotFoundError("Order not found")
        if order["status"] != "pending_payment":
            raise ValidationError("Order is not awaiting payment")
        return await self.set_status(order_id, "paid", note)
