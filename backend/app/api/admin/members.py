"""
app/api/admin/members.py — Admin member management.

GET    /admin/members           — list all users
DELETE /admin/members/{user_id} — deactivate a user
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.db.database import get_db
from app.db.models import LearnerBook, Progress, User

router = APIRouter(prefix="/admin/members", tags=["admin"])


class MemberOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    is_active: bool
    created_at: str




class MessageResponse(BaseModel):
    message: str


@router.get("", response_model=list[MemberOut])
async def list_members(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[MemberOut]:
    result = await db.execute(select(User).order_by(User.created_at))
    return [
        MemberOut(
            id=u.id, email=u.email, display_name=u.display_name,
            role=u.role, is_active=u.is_active,
            created_at=u.created_at.isoformat(),
        )
        for u in result.scalars().all()
    ]


@router.get("/overview")
async def overview(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Real platform figures for the admin overview."""
    week_ago = datetime.utcnow() - timedelta(days=7)
    learners = (await db.execute(select(func.count(User.id)).where(User.role == "learner"))).scalar_one()
    joined = (await db.execute(select(func.count(User.id)).where(User.role == "learner", User.created_at >= week_ago))).scalar_one()
    books = (await db.execute(select(func.count(LearnerBook.id)))).scalar_one()
    pages = (await db.execute(select(func.count(Progress.id)))).scalar_one()
    return {"learners": learners, "joinedThisWeek": joined, "books": books, "pagesRead": pages}


@router.delete("/{user_id}", response_model=MessageResponse)
async def remove_member(
    user_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    await db.commit()
    return MessageResponse(message=f"User {user.email} deactivated")
