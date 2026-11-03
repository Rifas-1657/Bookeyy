"""
app/books/access.py — who may see a course.

Seeded / admin-published courses are public.  Learner books (`learner_books`
rows with visibility "private") are visible only to their owner and admins.
Callers that 404 instead of 403 avoid leaking which private ids exist.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LearnerBook, User


def is_admin(user: User | None) -> bool:
    return bool(user and user.role == "admin")


async def private_books(db: AsyncSession) -> dict[str, LearnerBook]:
    """course_id → LearnerBook for every private learner book."""
    rows = (await db.execute(select(LearnerBook).where(LearnerBook.visibility == "private"))).scalars().all()
    return {b.course_id: b for b in rows}


async def book_for_course(db: AsyncSession, course_id: str) -> LearnerBook | None:
    return (await db.execute(select(LearnerBook).where(LearnerBook.course_id == course_id))).scalar_one_or_none()


def can_view(book: LearnerBook | None, user: User | None) -> bool:
    if book is None or book.visibility != "private":
        return True
    return user is not None and (user.id == book.owner_id or is_admin(user))


async def ensure_course_visible(db: AsyncSession, course_id: str, user: User | None) -> LearnerBook | None:
    book = await book_for_course(db, course_id)
    if not can_view(book, user):
        raise HTTPException(status_code=404, detail=f"Course {course_id!r} not found")
    return book


async def user_can_view_course(course_id: str, user_id: str | None) -> bool:
    """Session-less variant (tutor / websocket paths).

    Also covers courses whose Course row does not exist yet (a learner book or an
    admin course-builder job still being written): then only the job's creator
    (or an admin) may see it.  Callers should treat exceptions as "no access"."""
    from app.db.database import AsyncSessionLocal
    from app.db.models import Course, CourseBuildJob
    async with AsyncSessionLocal() as db:
        book = await book_for_course(db, course_id)
        user = await db.get(User, user_id) if user_id else None
        if book is not None:
            if book.visibility != "private":
                return True
            return user is not None and (user.id == book.owner_id or is_admin(user))
        if await db.get(Course, course_id) is not None:
            return True  # seeded / admin-published course: public
        job = (await db.execute(select(CourseBuildJob).where(CourseBuildJob.course_id == course_id)
                                .limit(1))).scalar_one_or_none()
        if job is None:
            return True  # unknown course id: nothing private to protect
        return user is not None and (user.id == job.created_by or is_admin(user))
