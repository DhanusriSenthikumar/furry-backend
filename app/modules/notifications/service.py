"""In-app notifications.

Email is a good way to tell someone something once, and a bad way to let them
catch up. Every lifecycle event that already sends mail also lands here, so a
customer who returns to the site a week later can see what happened while they
were away without digging through an inbox.

The important property of this module is that it is *unfailable*: `push` never
raises. It is called from the middle of order, return, and payment flows, and a
notification that cannot be written must never be the reason an order breaks.
"""

from datetime import datetime, timezone

from app.modules.notifications.repository import NotificationRepository


class NotificationService:
    def __init__(self, repo: NotificationRepository):
        self.repo = repo

    async def push(
        self,
        user_id: str,
        kind: str,
        title: str,
        body: str = "",
        link: str = "",
        dedupe_key: str | None = None,
    ) -> dict | None:
        """Record something the customer should know about.

        Swallows every error by design — see the module docstring. Returns the
        stored notification, or None if it was a duplicate or could not be
        written.
        """
        if not user_id:
            return None
        try:
            return await self.repo.push(
                {
                    "user_id": user_id,
                    "kind": kind,
                    "title": title,
                    "body": body,
                    "link": link,
                    "read_at": None,
                    "created_at": datetime.now(timezone.utc),
                },
                dedupe_key,
            )
        except Exception as exc:
            print(f"Warning: could not record notification for {user_id}: {exc}")
            return None

    async def feed(self, user_id: str, unread_only: bool = False, limit: int = 50) -> dict:
        return {
            "items": await self.repo.find_for_user(user_id, unread_only=unread_only, limit=limit),
            "unread": await self.repo.count_unread(user_id),
        }

    async def unread_count(self, user_id: str) -> int:
        return await self.repo.count_unread(user_id)

    async def mark_read(self, notification_id: str, user_id: str) -> None:
        await self.repo.mark_read(notification_id, user_id, datetime.now(timezone.utc))

    async def mark_all_read(self, user_id: str) -> int:
        return await self.repo.mark_all_read(user_id, datetime.now(timezone.utc))

    async def dismiss(self, notification_id: str, user_id: str) -> None:
        await self.repo.delete_for_user(notification_id, user_id)

    async def clear_read(self, user_id: str) -> int:
        return await self.repo.clear_read(user_id)

    # ------------------------------------------------------------------ #
    # Event helpers
    #
    # Wording lives here rather than at each call site so the feed reads as one
    # voice, and so a phrasing change is one edit instead of a search.
    # ------------------------------------------------------------------ #

    @staticmethod
    def _order_ref(order: dict) -> str:
        return str(order["_id"])[-8:]

    async def order_status_changed(self, order: dict, status: str, note: str = "") -> None:
        ref = self._order_ref(order)
        link = f"/orders/{order['_id']}"
        messages: dict[str, tuple[str, str, str]] = {
            "paid": ("order", f"Payment confirmed for order #{ref}", "We're getting it ready to ship."),
            "processing": ("order", f"Order #{ref} is being packed", "It'll be on its way shortly."),
            "shipped": ("shipment", f"Order #{ref} is on its way", note or "Track the parcel from your order page."),
            "delivered": ("order", f"Order #{ref} was delivered", "Let us know how it went by leaving a review."),
            "cancelled": ("order", f"Order #{ref} was cancelled", note or "Any reserved stock has been released."),
            "refunded": ("refund", f"Order #{ref} was fully refunded", note or "The money is on its way back to you."),
            "payment_failed": ("order", f"Payment failed for order #{ref}", "You can try paying again from the order page."),
        }
        entry = messages.get(status)
        if entry is None:
            return
        kind, title, body = entry
        # Keyed on the transition, so re-saving a status doesn't repeat it.
        await self.push(order["user_id"], kind, title, body, link, dedupe_key=f"order:{order['_id']}:{status}")

    async def question_answered(self, question: dict) -> None:
        await self.push(
            question["user_id"],
            "question",
            "Your question was answered",
            f"Staff replied about {question.get('product_name', 'a product')}.",
            f"/products/{question.get('product_slug', '')}",
            dedupe_key=f"question:{question['_id']}:answered",
        )

    async def back_in_stock(self, user_id: str, product: dict) -> None:
        await self.push(
            user_id,
            "stock",
            f"{product['name']} is back in stock",
            "You asked to hear when it returned — it's available now.",
            f"/products/{product.get('slug', '')}",
        )

    async def return_decided(self, ret: dict, status: str, note: str = "") -> None:
        ref = str(ret["_id"])[-8:]
        titles = {
            "approved": (f"Return #{ref} was approved", note or "Send the items back and we'll refund you."),
            "rejected": (f"Return #{ref} was declined", note or "Open a support ticket if you'd like us to take another look."),
            "refunded": (f"Refund issued for return #{ref}", note or "The money is on its way back to you."),
        }
        entry = titles.get(status)
        if entry is None:
            return
        title, body = entry
        await self.push(
            ret["user_id"],
            "refund" if status == "refunded" else "return",
            title,
            body,
            "/returns",
            dedupe_key=f"return:{ret['_id']}:{status}",
        )
