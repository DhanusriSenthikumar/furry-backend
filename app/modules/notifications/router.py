from fastapi import APIRouter, Depends, Query

from app.deps import get_current_user, get_db
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.schemas import NotificationFeedOut, NotificationOut
from app.modules.notifications.service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _service(db=Depends(get_db)) -> NotificationService:
    return NotificationService(NotificationRepository(db))


def notification_out(doc: dict) -> NotificationOut:
    created = doc["created_at"]
    return NotificationOut(
        id=str(doc["_id"]),
        kind=doc.get("kind", "system"),
        title=doc["title"],
        body=doc.get("body", ""),
        link=doc.get("link", ""),
        read=doc.get("read_at") is not None,
        created_at=created.isoformat() if hasattr(created, "isoformat") else str(created),
    )


@router.get("", response_model=NotificationFeedOut)
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=30, ge=1, le=100),
    user: dict = Depends(get_current_user),
    service: NotificationService = Depends(_service),
):
    """The feed and the unread count together — the bell badge and the panel are
    drawn from one response so they can never disagree."""
    feed = await service.feed(str(user["_id"]), unread_only=unread_only, limit=limit)
    return NotificationFeedOut(items=[notification_out(n) for n in feed["items"]], unread=feed["unread"])


@router.post("/read-all")
async def mark_all_read(user: dict = Depends(get_current_user), service: NotificationService = Depends(_service)):
    return {"marked": await service.mark_all_read(str(user["_id"]))}


@router.delete("/read")
async def clear_read(user: dict = Depends(get_current_user), service: NotificationService = Depends(_service)):
    """Tidy up the feed without losing anything still unread."""
    return {"deleted": await service.clear_read(str(user["_id"]))}


@router.post("/{notification_id}/read", status_code=204)
async def mark_read(
    notification_id: str,
    user: dict = Depends(get_current_user),
    service: NotificationService = Depends(_service),
):
    await service.mark_read(notification_id, str(user["_id"]))


@router.delete("/{notification_id}", status_code=204)
async def dismiss(
    notification_id: str,
    user: dict = Depends(get_current_user),
    service: NotificationService = Depends(_service),
):
    await service.dismiss(notification_id, str(user["_id"]))
