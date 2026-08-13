"""Customer support tickets.

Everything else in the store is a form: a return has fields, a review has a
rating. Support is what is left when the customer's problem doesn't fit any of
them, so a ticket is deliberately just a subject and a conversation.

The field that makes the queue work is `awaiting`. Every message sets it to
whoever owes the next reply, which means "how many people are waiting on us" is
a count rather than a judgement call, and a ticket can never be quietly dropped
because it looked answered.
"""

import uuid
from datetime import datetime, timezone

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.pagination import Pagination
from app.modules.notifications.service import NotificationService
from app.modules.orders.repository import OrderRepository
from app.modules.support.repository import SupportRepository

# A resolved ticket reopens if the customer writes again — their reply is the
# evidence it wasn't resolved. A closed one doesn't; closing is final.
REOPENABLE_STATUSES = {"resolved"}


class SupportService:
    def __init__(
        self,
        repo: SupportRepository,
        notifications: NotificationService | None = None,
        orders: OrderRepository | None = None,
    ):
        self.repo = repo
        self.notifications = notifications
        # Only used to check that an attached order is really the customer's.
        self.orders = orders

    @staticmethod
    def _message(author: dict, body: str, is_staff: bool, now: datetime) -> dict:
        return {
            "id": uuid.uuid4().hex,
            "author_id": str(author["_id"]),
            "author_name": author.get("name", "Support" if is_staff else "Customer"),
            "is_staff": is_staff,
            "body": body.strip(),
            "created_at": now,
        }

    async def _get(self, ticket_id: str) -> dict:
        ticket = await self.repo.find_by_id(ticket_id)
        if not ticket:
            raise NotFoundError("Ticket not found")
        return ticket

    async def get_owned(self, ticket_id: str, user: dict) -> dict:
        ticket = await self._get(ticket_id)
        if ticket["user_id"] != str(user["_id"]) and not user.get("is_admin"):
            raise ForbiddenError("Not allowed to view this ticket")
        return ticket

    # ------------------------------------------------------------------ #
    # The customer's side
    # ------------------------------------------------------------------ #

    async def create(self, user: dict, payload) -> dict:
        user_id = str(user["_id"])

        order_id = None
        if payload.order_id:
            # Checked rather than trusted: an order reference on a ticket is
            # shown to staff, and pointing at somebody else's order would leak
            # its contents into this conversation.
            if self.orders is not None:
                order = await self.orders.find_by_id(payload.order_id)
                if not order or order["user_id"] != user_id:
                    raise ValidationError("That order isn't on your account")
            order_id = payload.order_id

        now = datetime.now(timezone.utc)
        return await self.repo.insert(
            {
                "user_id": user_id,
                "user_name": user.get("name", ""),
                "user_email": user.get("email", ""),
                "subject": payload.subject.strip(),
                "category": payload.category,
                "status": "open",
                "priority": "normal",
                "order_id": order_id,
                "assigned_to": "",
                "messages": [self._message(user, payload.body, False, now)],
                "awaiting": "staff",
                "customer_unread": False,
                "created_at": now,
                "updated_at": now,
                "last_message_at": now,
            }
        )

    async def list_mine(self, user_id: str, status: str | None = None) -> list[dict]:
        return await self.repo.find_by_user(user_id, status)

    async def unread_count(self, user_id: str) -> int:
        return await self.repo.count_unread_for_user(user_id)

    async def mark_seen(self, ticket_id: str, user: dict) -> dict:
        """Clear the customer's unread flag. Opening a thread is reading it."""
        ticket = await self.get_owned(ticket_id, user)
        if ticket["user_id"] == str(user["_id"]) and ticket.get("customer_unread"):
            return await self.repo.update_by_id(ticket_id, {"customer_unread": False})
        return ticket

    async def reply(self, ticket_id: str, user: dict, body: str) -> dict:
        """Add a message from either side. Who is replying decides everything
        else: who is waited on next, whether the customer gets told, and whether
        a resolved ticket comes back to life."""
        ticket = await self.get_owned(ticket_id, user)
        is_staff = bool(user.get("is_admin")) and ticket["user_id"] != str(user["_id"])

        if ticket["status"] == "closed":
            raise ValidationError("This ticket is closed — open a new one and we'll pick it up there")

        now = datetime.now(timezone.utc)
        update: dict = {
            "awaiting": "customer" if is_staff else "staff",
            "last_message_at": now,
            "updated_at": now,
            # Staff replying is the one thing the customer needs to come back for.
            "customer_unread": is_staff,
        }

        if is_staff:
            # Staff answering moves it to "waiting on them" rather than closing
            # it — only a person decides a problem is solved.
            update["status"] = "pending"
        elif ticket["status"] in REOPENABLE_STATUSES:
            update["status"] = "open"

        updated = await self.repo.append_message(
            ticket_id, self._message(user, body, is_staff, now), update
        )

        if is_staff and self.notifications is not None:
            await self.notifications.push(
                ticket["user_id"],
                "support",
                f"Support replied about \"{ticket['subject']}\"",
                body.strip()[:140],
                f"/support/{ticket_id}",
            )
        return updated

    async def close_own(self, ticket_id: str, user: dict) -> dict:
        """The customer marking their own problem solved."""
        ticket = await self.get_owned(ticket_id, user)
        if ticket["status"] == "closed":
            return ticket

        now = datetime.now(timezone.utc)
        return await self.repo.update_by_id(
            ticket_id,
            {"status": "closed", "awaiting": "nobody", "updated_at": now, "customer_unread": False},
        )

    # ------------------------------------------------------------------ #
    # Staff
    # ------------------------------------------------------------------ #

    async def list_all(
        self, pagination: Pagination, status: str | None = None, priority: str | None = None
    ) -> list[dict]:
        return await self.repo.find_all(
            skip=pagination.skip, limit=pagination.page_size, status=status, priority=priority
        )

    async def triage(self, ticket_id: str, payload) -> dict:
        ticket = await self._get(ticket_id)
        update = {k: v for k, v in payload.model_dump().items() if v is not None}
        if not update:
            return ticket

        now = datetime.now(timezone.utc)
        update["updated_at"] = now

        new_status = update.get("status")
        if new_status in ("resolved", "closed"):
            update["awaiting"] = "nobody"
        elif new_status == "open":
            update["awaiting"] = "staff"

        updated = await self.repo.update_by_id(ticket_id, update)

        # A status change the customer would want to know about, without a
        # message attached to explain it.
        if new_status in ("resolved", "closed") and ticket["status"] != new_status and self.notifications:
            await self.notifications.push(
                ticket["user_id"],
                "support",
                f"Your ticket \"{ticket['subject']}\" was marked {new_status}",
                "Reply on the thread if there's anything still outstanding.",
                f"/support/{ticket_id}",
                dedupe_key=f"support:{ticket_id}:{new_status}",
            )
        return updated

    async def stats(self) -> dict:
        counts = await self.repo.stats()
        oldest = await self.repo.oldest_waiting()
        return {
            "open": counts.get("open", 0),
            "pending": counts.get("pending", 0),
            "resolved": counts.get("resolved", 0),
            "closed": counts.get("closed", 0),
            "awaiting_staff": await self.repo.count_awaiting_staff(),
            "oldest_waiting": oldest.isoformat() if hasattr(oldest, "isoformat") else None,
        }
