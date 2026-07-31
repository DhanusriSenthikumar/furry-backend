"""Returns and refunds — the half of the order lifecycle that runs backwards.

A return is a record of its own rather than another order status, because the
two don't line up: a customer can send back three of five items, and the order
is still legitimately "delivered" while that return is being decided. Only a
return that gives back every remaining dollar flips the order to "refunded".

Money is never computed here. `price_refund` in `app/core/pricing.py` owns that,
the same way `price_order` owns what the customer was charged.
"""

import asyncio
from datetime import datetime, timezone

from app.core.email import email_service
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.pagination import Pagination
from app.core.pricing import price_refund
from app.modules.orders.service import OrderService
from app.modules.payments.razorpay_client import RazorpayClient
from app.modules.payments.repository import PaymentRepository
from app.modules.payments.stripe_client import StripeClient
from app.modules.products.repository import ProductRepository
from app.modules.returns.repository import ReturnRepository
from app.modules.returns.schemas import ReturnCreate, ReturnRefund, ReturnResolve
from app.modules.stock_alerts.service import StockAlertService
from app.modules.users.repository import UserRepository


class ReturnService:
    def __init__(
        self,
        repo: ReturnRepository,
        orders: OrderService,
        products: ProductRepository,
        users: UserRepository | None = None,
        payments: PaymentRepository | None = None,
        stripe_client: StripeClient | None = None,
        razorpay_client: RazorpayClient | None = None,
        alerts: StockAlertService | None = None,
    ):
        self.repo = repo
        self.orders = orders
        self.products = products
        self.users = users
        # Goods coming back onto the shelf can settle someone else's wait.
        self.alerts = alerts
        # All optional: without them a refund is still recorded and the customer
        # still gets their money owed on the books — it just has to be settled by
        # hand, the same fallback the checkout gateways use.
        self.payments = payments
        self.stripe_client = stripe_client
        self.razorpay_client = razorpay_client

    # ------------------------------------------------------------------ #
    # What's still returnable
    # ------------------------------------------------------------------ #

    async def _claimed_units(self, order_id: str) -> dict[str, int]:
        """Units of this order already spoken for by another return."""
        claimed: dict[str, int] = {}
        for existing in await self.repo.find_claiming_by_order(order_id):
            for item in existing.get("items", []):
                claimed[item["product_id"]] = claimed.get(item["product_id"], 0) + item["quantity"]
        return claimed

    async def _returnable(self, order: dict) -> dict[str, int]:
        claimed = await self._claimed_units(str(order["_id"]))
        return {
            item["product_id"]: max(item["quantity"] - claimed.get(item["product_id"], 0), 0)
            for item in order.get("items", [])
        }

    async def eligibility(self, order_id: str, user: dict) -> dict:
        """Everything the return form needs: whether it's allowed, why not, and
        how much of each line is still sendable back."""
        order = await self.orders.get_owned(order_id, user)
        can_return, reason = OrderService.return_eligibility(order)
        returnable = await self._returnable(order)

        if can_return and not any(returnable.values()):
            can_return, reason = False, "Every item on this order has already been returned"

        window_ends = OrderService.return_window_ends(order)
        return {
            "order_id": order_id,
            "can_return": can_return,
            "reason": reason,
            "return_window_ends": window_ends.isoformat() if window_ends else None,
            "items": [
                {
                    "product_id": item["product_id"],
                    "name": item["name"],
                    "price": item["price"],
                    "quantity_ordered": item["quantity"],
                    "quantity_returnable": returnable.get(item["product_id"], 0),
                }
                for item in order.get("items", [])
            ],
        }

    # ------------------------------------------------------------------ #
    # Customer
    # ------------------------------------------------------------------ #

    async def request(self, user: dict, payload: ReturnCreate) -> dict:
        order = await self.orders.get_owned(payload.order_id, user)

        can_return, reason = OrderService.return_eligibility(order)
        if not can_return:
            raise ValidationError(reason)

        returnable = await self._returnable(order)
        order_lines = {item["product_id"]: item for item in order.get("items", [])}

        items: list[dict] = []
        requested: dict[str, int] = {}
        for line in payload.items:
            ordered = order_lines.get(line.product_id)
            if not ordered:
                raise ValidationError("That item isn't on this order")

            available = returnable.get(line.product_id, 0)
            if available <= 0:
                raise ValidationError(f"{ordered['name']} has already been returned")
            if line.quantity > available:
                raise ValidationError(
                    f"Only {available} of {ordered['name']} can still be returned"
                )

            requested[line.product_id] = requested.get(line.product_id, 0) + line.quantity
            if requested[line.product_id] > available:
                raise ValidationError(f"Too many of {ordered['name']} in this request")

            items.append(
                {
                    "product_id": line.product_id,
                    "name": ordered["name"],
                    "price": ordered["price"],
                    "quantity": line.quantity,
                    "reason": line.reason,
                }
            )

        breakdown = price_refund(order, requested, self._is_full_return(returnable, requested))
        now = datetime.now(timezone.utc)
        doc = {
            "user_id": str(user["_id"]),
            "order_id": payload.order_id,
            "order_ref": payload.order_id[-8:],
            "items": items,
            "comment": payload.comment.strip(),
            "status": "requested",
            "refund_estimate": breakdown.total,
            "refund_breakdown": breakdown.as_dict(),
            "refund_amount": 0.0,
            "refund_method": "",
            "refund_reference": "",
            "restocked": False,
            "resolution_note": "",
            "resolved_by": "",
            "created_at": now,
            "updated_at": now,
        }
        created = await self.repo.insert(doc)

        await self._notify(email_service.send_return_requested, user["email"], user["name"], created)
        return created

    @staticmethod
    def _is_full_return(returnable: dict[str, int], requested: dict[str, int]) -> bool:
        """True when nothing on the order is left un-returned after this request —
        the only case where the delivery fee comes back too."""
        return all(requested.get(product_id, 0) >= remaining for product_id, remaining in returnable.items())

    async def list_mine(self, user_id: str) -> list[dict]:
        return await self.repo.find_by_user(user_id)

    async def get_owned(self, return_id: str, user: dict) -> dict:
        ret = await self.repo.find_by_id(return_id)
        if not ret:
            raise NotFoundError("Return not found")
        if ret["user_id"] != str(user["_id"]) and not user.get("is_admin"):
            raise ForbiddenError("Not allowed to view this return")
        return ret

    # ------------------------------------------------------------------ #
    # Staff
    # ------------------------------------------------------------------ #

    async def list_all(self, pagination: Pagination, status: str | None = None) -> list[dict]:
        return await self.repo.find_all(skip=pagination.skip, limit=pagination.page_size, status=status)

    async def count_pending(self) -> int:
        return await self.repo.count_pending()

    async def _require_requested(self, return_id: str) -> dict:
        ret = await self.repo.find_by_id(return_id)
        if not ret:
            raise NotFoundError("Return not found")
        if ret["status"] != "requested":
            raise ValidationError(f"This return has already been {ret['status']}")
        return ret

    async def approve(self, return_id: str, admin: dict, payload: ReturnResolve) -> dict:
        await self._require_requested(return_id)
        return await self._resolve(return_id, "approved", admin, payload.note)

    async def reject(self, return_id: str, admin: dict, payload: ReturnResolve) -> dict:
        await self._require_requested(return_id)
        return await self._resolve(return_id, "rejected", admin, payload.note)

    async def _resolve(self, return_id: str, status: str, admin: dict, note: str) -> dict:
        updated = await self.repo.update_by_id(
            return_id,
            {
                "status": status,
                "resolution_note": note.strip(),
                "resolved_by": admin.get("name", ""),
                "updated_at": datetime.now(timezone.utc),
            },
        )
        await self._email_customer(updated, email_service.send_return_decision)
        return updated

    async def refund(self, return_id: str, admin: dict, payload: ReturnRefund) -> dict:
        """Settle an approved return: put the goods back (unless they came back
        damaged), send the money, and write it onto the order."""
        ret = await self.repo.find_by_id(return_id)
        if not ret:
            raise NotFoundError("Return not found")
        if ret["status"] == "refunded":
            raise ValidationError("This return has already been refunded")
        if ret["status"] != "approved":
            raise ValidationError("Only an approved return can be refunded")

        order = await self.orders.repo.find_by_id(ret["order_id"])
        if not order:
            raise NotFoundError("The order behind this return no longer exists")

        # Re-price at settlement rather than trusting the estimate: another
        # return on the same order may have been paid out in the meantime, and
        # `price_refund` caps against what's actually left.
        requested = {item["product_id"]: item["quantity"] for item in ret["items"]}
        returnable = await self._returnable_excluding(order, return_id)
        breakdown = price_refund(order, requested, self._is_full_return(returnable, requested))
        amount = round(payload.amount, 2) if payload.amount is not None else breakdown.total

        if payload.restock:
            for item in ret["items"]:
                await self.products.restore_stock(item["product_id"], item["quantity"])
                if self.alerts is not None:
                    await self.alerts.flush(item["product_id"])

        method, reference, gateway_note = await self._settle(order, amount)

        note = payload.note.strip() or gateway_note
        updated = await self.repo.update_by_id(
            return_id,
            {
                "status": "refunded",
                "refund_amount": amount,
                "refund_breakdown": breakdown.as_dict(),
                "refund_method": method,
                "refund_reference": reference,
                "restocked": payload.restock,
                "resolution_note": note,
                "resolved_by": admin.get("name", ""),
                "updated_at": datetime.now(timezone.utc),
            },
        )

        await self.orders.record_refund(
            ret["order_id"], amount, f"Refunded ${amount:.2f} for return #{return_id[-8:]}"
        )
        await self._email_customer(updated, email_service.send_refund_issued)
        return updated

    async def _returnable_excluding(self, order: dict, return_id: str) -> dict[str, int]:
        """Returnable units treating `return_id` as not yet claimed — so the
        return being settled is measured against the order as it stood."""
        claimed: dict[str, int] = {}
        for existing in await self.repo.find_claiming_by_order(str(order["_id"])):
            if str(existing["_id"]) == return_id:
                continue
            for item in existing.get("items", []):
                claimed[item["product_id"]] = claimed.get(item["product_id"], 0) + item["quantity"]
        return {
            item["product_id"]: max(item["quantity"] - claimed.get(item["product_id"], 0), 0)
            for item in order.get("items", [])
        }

    async def _settle(self, order: dict, amount: float) -> tuple[str, str, str]:
        """Push the refund through whichever gateway took the money.

        Returns (method, gateway reference, note). A gateway that isn't
        configured — or a cash order — settles as "manual": the amount is on the
        books and staff square it up, rather than the whole refund failing.
        """
        manual = ("manual", "", f"Refunded ${amount:.2f} — settle manually")
        if amount <= 0:
            return "manual", "", "No money owed on this return"
        if self.payments is None:
            return manual

        payment = await self.payments.find_by_order(str(order["_id"]))
        if not payment or payment.get("status") != "succeeded":
            return manual

        gateway = payment.get("gateway", "")
        try:
            if gateway == "stripe" and self.stripe_client is not None:
                result = await asyncio.to_thread(
                    self.stripe_client.refund, payment["gateway_reference"], amount
                )
                return "stripe", result["id"], f"Refunded ${amount:.2f} via Stripe"
            if gateway == "razorpay" and self.razorpay_client is not None:
                # Set when the payment was verified; without it there's nothing
                # for Razorpay to refund against.
                payment_id = payment.get("gateway_payment_id", "")
                if not payment_id:
                    return "manual", "", f"Refunded ${amount:.2f} — no Razorpay payment id on file"
                result = await asyncio.to_thread(self.razorpay_client.refund, payment_id, amount)
                return "razorpay", str(result["id"]), f"Refunded ${amount:.2f} via Razorpay"
        except Exception as exc:
            # Cash on delivery lands here too via the fall-through below.
            print(f"Warning: {gateway} refund failed, recording as manual: {exc}")
            return "manual", "", f"Refunded ${amount:.2f} — {gateway} refund failed, settle manually"

        return manual

    # ------------------------------------------------------------------ #

    async def _email_customer(self, ret: dict, template) -> None:
        if self.users is None:
            return
        customer = await self.users.find_by_id(ret["user_id"])
        if customer:
            await self._notify(template, customer["email"], customer.get("name", "there"), ret)

    @staticmethod
    async def _notify(fn, *args) -> None:
        """A refund must never fail because SMTP did."""
        try:
            await asyncio.to_thread(fn, *args)
        except Exception as exc:
            print(f"Warning: could not send email: {exc}")
