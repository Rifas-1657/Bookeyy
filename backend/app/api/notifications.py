"""
app/api/notifications.py — Notifications (IDOR-safe).

GET    /me/notifications
POST   /me/notifications/{id}/read
POST   /me/notifications/read-all
DELETE /me/notifications/{id}

Kinds the app creates: "book_ready" (personal book built, app/books/personal.py) and
"review_due" (spaced reviews waiting; created lazily here, at most once a day).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_db
from app.db.models import Notification, User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/me/notifications", tags=["notifications"])

# Where each kind takes the learner; unknown kinds get no link.
KIND_HREF = {"book_ready": "/courses/python", "review_due": "/progress", "verify_email": "/settings"}


class NotificationOut(BaseModel):
    id: str
    kind: str
    title: str
    body: str
    read: bool
    created_at: str
    href: str | None = None


class MessageResponse(BaseModel):
    message: str


async def _note_due_reviews(db: AsyncSession, user_id: str) -> None:
    """One "reviews due" notification a day while spaced reviews are waiting."""
    from app.personalization.learner_model import due_reviews

    since = datetime.utcnow() - timedelta(hours=20)
    recent = (await db.execute(select(Notification.id).where(
        Notification.user_id == user_id, Notification.kind == "review_due",
        Notification.created_at >= since).limit(1))).first()
    if recent is not None:
        return
    try:
        due = await due_reviews(db, user_id)
    except Exception:  # never let the bell break on the learner model
        log.warning("due review check failed", exc_info=True)
        return
    if not due:
        return
    n = len(due)
    names = ", ".join(d["label"] for d in due[:2])
    db.add(Notification(user_id=user_id, kind="review_due",
                        title=f"{n} quick review{'s' if n != 1 else ''} due",
                        body=f"A two-minute refresher keeps it fresh: {names}{' and more' if n > 2 else ''}."))
    await db.commit()


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[NotificationOut]:
    await _note_due_reviews(db, current_user.id)
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
    )
    return [
        NotificationOut(
            id=n.id, kind=n.kind, title=n.title, body=n.body,
            read=n.read, created_at=n.created_at.isoformat(), href=KIND_HREF.get(n.kind),
        )
        for n in result.scalars().all()
    ]


@router.post("/{notification_id}/read", response_model=MessageResponse)
async def mark_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
    )
    notif = result.scalar_one_or_none()
    if notif is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.read = True
    await db.commit()
    return MessageResponse(message="Marked as read")


@router.post("/read-all", response_model=MessageResponse)
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    result = await db.execute(
        select(Notification).where(
            Notification.user_id == current_user.id,
            Notification.read == False,  # noqa: E712
        )
    )
    for n in result.scalars().all():
        n.read = True
    await db.commit()
    return MessageResponse(message="All notifications marked as read")


@router.delete("/{notification_id}", status_code=204)
async def delete_notification(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    notif = (await db.execute(select(Notification).where(
        Notification.id == notification_id, Notification.user_id == current_user.id))).scalar_one_or_none()
    if notif is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.delete(notif)
    await db.commit()
    return Response(status_code=204)
