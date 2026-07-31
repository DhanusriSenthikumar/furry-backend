"""Back-in-stock alerts.

An out-of-stock product is otherwise a dead end: the shopper leaves and we never
learn they wanted it. A stock alert turns that moment into two things — a
promise to the customer, and a demand signal for whoever decides what to reorder.

Alerts are flushed by `flush`, which is safe to call after *any* stock change
from any path (admin edit, cancelled order, restocked return, MCP). It no-ops
when the shelf is still empty or nobody is waiting, so callers don't have to
work out whether a restock actually happened.
"""

import asyncio
import uuid
from datetime import datetime, timezone

from app.core.email import email_service
from app.core.exceptions import NotFoundError, ValidationError
from app.modules.products.repository import ProductRepository
from app.modules.stock_alerts.repository import StockAlertRepository
from app.modules.users.repository import UserRepository


class StockAlertService:
    def __init__(
        self,
        repo: StockAlertRepository,
        products: ProductRepository,
        users: UserRepository | None = None,
    ):
        self.repo = repo
        self.products = products
        # Only needed to address the email. Without it the alert is still
        # recorded and still counts as demand; nobody just gets told.
        self.users = users

    async def _product(self, product_id: str) -> dict:
        product = await self.products.find_by_id(product_id)
        if not product:
            raise NotFoundError("Product not found")
        return product

    async def status(self, product_id: str, user_id: str | None) -> dict:
        product = await self._product(product_id)
        alert = await self.repo.find_one_for(user_id, product_id) if user_id else None
        return {
            "product_id": str(product["_id"]),
            "subscribed": bool(alert and alert.get("notified_at") is None),
            "waiting": await self.repo.count_pending(product_id),
        }

    async def subscribe(self, product_id: str, user_id: str) -> dict:
        product = await self._product(product_id)
        if product.get("stock", 0) > 0:
            raise ValidationError("This product is already in stock")

        await self.repo.subscribe(user_id, product_id, datetime.now(timezone.utc))
        return await self.status(product_id, user_id)

    async def unsubscribe(self, product_id: str, user_id: str) -> None:
        await self.repo.unsubscribe(user_id, product_id)

    # ------------------------------------------------------------------ #
    # Delivery
    # ------------------------------------------------------------------ #

    async def flush(self, product_id: str) -> int:
        """Email everyone waiting on this product, if it is actually back.
        Returns how many people were told."""
        product = await self.products.find_by_id(product_id)
        if not product or product.get("stock", 0) <= 0:
            return 0

        batch = uuid.uuid4().hex
        claimed = await self.repo.claim_pending(product_id, batch, datetime.now(timezone.utc))
        if not claimed:
            return 0

        sent = 0
        for alert in claimed:
            if await self._notify_one(alert, product):
                sent += 1
        return sent

    async def _notify_one(self, alert: dict, product: dict) -> bool:
        if self.users is None:
            return False

        customer = await self.users.find_by_id(alert["user_id"])
        if not customer or not customer.get("is_active", True):
            # Nobody to write to. The row stays spent rather than being released,
            # so a deactivated account can't jam the queue on every restock.
            return True

        try:
            # SMTP off the event loop, the same way order emails are sent.
            await asyncio.to_thread(
                email_service.send_back_in_stock,
                customer["email"],
                customer.get("name", "there"),
                product,
            )
            return True
        except Exception as exc:
            print(f"Warning: could not send back-in-stock email: {exc}")
            await self.repo.release(str(alert["_id"]))
            return False

    # ------------------------------------------------------------------ #
    # Admin
    # ------------------------------------------------------------------ #

    async def demand(self, limit: int = 50) -> list[dict]:
        """The restock queue ordered by how many customers are waiting, with
        enough product detail to act on a row without opening it."""
        rows = []
        for group in await self.repo.demand(limit):
            product = await self.products.find_by_id(group["_id"])
            if not product:
                # The product was deleted out from under the requests.
                continue
            oldest = group.get("oldest_request")
            rows.append(
                {
                    "product_id": str(product["_id"]),
                    "name": product["name"],
                    "slug": product["slug"],
                    "image": (product.get("images") or [""])[0],
                    "stock": product.get("stock", 0),
                    "waiting": group["waiting"],
                    "oldest_request": oldest.isoformat() if hasattr(oldest, "isoformat") else str(oldest),
                }
            )
        return rows
