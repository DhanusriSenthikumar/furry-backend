from fastapi import APIRouter, Depends

from app.core.pagination import Pagination
from app.deps import get_current_admin, get_current_user, get_db, pagination_params
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.service import NotificationService
from app.modules.orders.repository import OrderRepository
from app.modules.support.repository import SupportRepository
from app.modules.support.schemas import (
    SupportStatsOut,
    TicketAdminUpdate,
    TicketCreate,
    TicketMessageOut,
    TicketOut,
    TicketReply,
    TicketSummaryOut,
)
from app.modules.support.service import SupportService

# No prefix: the customer's tickets live under /support, the staff queue under
# /admin, mirroring how returns and stock alerts are laid out.
router = APIRouter(tags=["support"])


def _service(db=Depends(get_db)) -> SupportService:
    return SupportService(
        SupportRepository(db),
        NotificationService(NotificationRepository(db)),
        OrderRepository(db),
    )


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def message_out(msg: dict) -> TicketMessageOut:
    return TicketMessageOut(
        id=msg.get("id", ""),
        author_name=msg.get("author_name", ""),
        is_staff=msg.get("is_staff", False),
        body=msg.get("body", ""),
        created_at=_iso(msg["created_at"]),
    )


def ticket_out(doc: dict) -> TicketOut:
    return TicketOut(
        id=str(doc["_id"]),
        user_id=doc["user_id"],
        user_name=doc.get("user_name", ""),
        user_email=doc.get("user_email", ""),
        subject=doc["subject"],
        category=doc.get("category", "other"),
        status=doc.get("status", "open"),
        priority=doc.get("priority", "normal"),
        order_id=doc.get("order_id"),
        assigned_to=doc.get("assigned_to", "") or "",
        messages=[message_out(m) for m in doc.get("messages", [])],
        awaiting=doc.get("awaiting", "staff"),
        customer_unread=doc.get("customer_unread", False),
        created_at=_iso(doc["created_at"]),
        last_message_at=_iso(doc.get("last_message_at", doc["created_at"])),
    )


def ticket_summary_out(doc: dict) -> TicketSummaryOut:
    messages = doc.get("messages", [])
    latest = messages[-1]["body"] if messages else ""
    return TicketSummaryOut(
        id=str(doc["_id"]),
        user_id=doc["user_id"],
        user_name=doc.get("user_name", ""),
        subject=doc["subject"],
        category=doc.get("category", "other"),
        status=doc.get("status", "open"),
        priority=doc.get("priority", "normal"),
        assigned_to=doc.get("assigned_to", "") or "",
        awaiting=doc.get("awaiting", "staff"),
        customer_unread=doc.get("customer_unread", False),
        message_count=len(messages),
        preview=latest[:160],
        created_at=_iso(doc["created_at"]),
        last_message_at=_iso(doc.get("last_message_at", doc["created_at"])),
    )


# ---------------------------------------------------------------------- #
# The customer's tickets
# ---------------------------------------------------------------------- #


@router.get("/support/tickets", response_model=list[TicketSummaryOut])
async def list_my_tickets(
    status: str | None = None,
    user: dict = Depends(get_current_user),
    service: SupportService = Depends(_service),
):
    return [ticket_summary_out(t) for t in await service.list_mine(str(user["_id"]), status)]


@router.post("/support/tickets", response_model=TicketOut, status_code=201)
async def open_ticket(
    payload: TicketCreate,
    user: dict = Depends(get_current_user),
    service: SupportService = Depends(_service),
):
    return ticket_out(await service.create(user, payload))


@router.get("/support/unread")
async def my_unread_tickets(
    user: dict = Depends(get_current_user), service: SupportService = Depends(_service)
):
    """Powers the "you have a reply" dot on the account page."""
    return {"unread": await service.unread_count(str(user["_id"]))}


@router.get("/support/tickets/{ticket_id}", response_model=TicketOut)
async def get_ticket(
    ticket_id: str,
    user: dict = Depends(get_current_user),
    service: SupportService = Depends(_service),
):
    """Opening a thread marks it read — reading it is what "seen" means."""
    return ticket_out(await service.mark_seen(ticket_id, user))


@router.post("/support/tickets/{ticket_id}/reply", response_model=TicketOut)
async def reply_to_ticket(
    ticket_id: str,
    payload: TicketReply,
    user: dict = Depends(get_current_user),
    service: SupportService = Depends(_service),
):
    """The same endpoint for both sides. Whether the reply counts as staff is
    decided from the session, never from the request."""
    return ticket_out(await service.reply(ticket_id, user, payload.body))


@router.post("/support/tickets/{ticket_id}/close", response_model=TicketOut)
async def close_ticket(
    ticket_id: str,
    user: dict = Depends(get_current_user),
    service: SupportService = Depends(_service),
):
    return ticket_out(await service.close_own(ticket_id, user))


# ---------------------------------------------------------------------- #
# Staff
# ---------------------------------------------------------------------- #


@router.get("/admin/support/tickets", response_model=list[TicketSummaryOut])
async def list_all_tickets(
    status: str | None = None,
    priority: str | None = None,
    pagination: Pagination = Depends(pagination_params),
    _admin: dict = Depends(get_current_admin),
    service: SupportService = Depends(_service),
):
    """The queue, longest-waiting first."""
    return [ticket_summary_out(t) for t in await service.list_all(pagination, status, priority)]


@router.get("/admin/support/stats", response_model=SupportStatsOut)
async def support_stats(
    _admin: dict = Depends(get_current_admin), service: SupportService = Depends(_service)
):
    return SupportStatsOut(**await service.stats())


@router.patch("/admin/support/tickets/{ticket_id}", response_model=TicketOut)
async def triage_ticket(
    ticket_id: str,
    payload: TicketAdminUpdate,
    _admin: dict = Depends(get_current_admin),
    service: SupportService = Depends(_service),
):
    """Set status, priority, or owner. Each field is independent, so the UI can
    offer them as separate one-click actions."""
    return ticket_out(await service.triage(ticket_id, payload))
