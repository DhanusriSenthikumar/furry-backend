from pydantic import BaseModel

from app.core.enums import NotificationKind


class NotificationOut(BaseModel):
    id: str
    kind: NotificationKind
    title: str
    body: str
    # Where clicking it should go, as a frontend path. Empty when the
    # notification is purely informational.
    link: str = ""
    read: bool
    created_at: str


class NotificationFeedOut(BaseModel):
    """The feed and its badge in one response — the bell needs both, and two
    round trips would let them disagree."""

    items: list[NotificationOut]
    unread: int
