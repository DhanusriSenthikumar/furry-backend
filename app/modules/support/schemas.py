from pydantic import BaseModel, Field

from app.core.enums import TicketCategory, TicketPriority, TicketStatus


class TicketCreate(BaseModel):
    subject: str = Field(min_length=3, max_length=140)
    category: TicketCategory = "other"
    body: str = Field(min_length=1, max_length=4000)
    # Attaching an order saves the first two replies being "which order?".
    order_id: str | None = None


class TicketReply(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class TicketAdminUpdate(BaseModel):
    """Staff triage. All optional — each field is its own small action in the UI."""

    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    # Free text rather than a user id: the queue is small and a name is what
    # everyone actually looks for.
    assigned_to: str | None = Field(default=None, max_length=100)


class TicketMessageOut(BaseModel):
    id: str
    author_name: str
    # Drives which side of the thread it renders on. Derived server-side so the
    # browser never decides who counts as staff.
    is_staff: bool
    body: str
    created_at: str


class TicketOut(BaseModel):
    id: str
    user_id: str
    user_name: str
    user_email: str
    subject: str
    category: TicketCategory
    status: TicketStatus
    priority: TicketPriority
    order_id: str | None = None
    assigned_to: str = ""
    messages: list[TicketMessageOut]
    # Who owes the next reply. The single most useful column in the queue.
    awaiting: str
    # There is something here the customer hasn't seen.
    customer_unread: bool = False
    created_at: str
    last_message_at: str


class TicketSummaryOut(BaseModel):
    """A row in a list — everything but the thread itself, which can be long."""

    id: str
    user_id: str
    user_name: str
    subject: str
    category: TicketCategory
    status: TicketStatus
    priority: TicketPriority
    assigned_to: str = ""
    awaiting: str
    customer_unread: bool = False
    message_count: int
    # First line of the latest message, so the queue is scannable without
    # opening every ticket.
    preview: str = ""
    created_at: str
    last_message_at: str


class SupportStatsOut(BaseModel):
    open: int
    pending: int
    resolved: int
    closed: int
    awaiting_staff: int
    # ISO timestamp of the longest-waiting unanswered ticket, so an ageing queue
    # is visible rather than just a count.
    oldest_waiting: str | None = None
