"""
app/api/surveys.py — Per-course personalization survey answers (IDOR-safe).

GET    /me/surveys               — all of the user's answers, keyed by course id
PUT    /me/surveys/{course_id}   — create or replace answers for a course
DELETE /me/surveys/{course_id}   — forget answers so the survey runs again

Answers mirror the frontend `Personalization` type. Bookey teaches only in
English and Tanglish, so any other language is rejected.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_db
from app.db.models import Course, CourseSurvey, User

router = APIRouter(prefix="/me/surveys", tags=["personalization"])

Interest = Literal[
    "movies", "cricket", "games", "music", "cooking", "cars", "anime", "business", "science",
]


class Accessibility(BaseModel):
    reducedMotion: bool = False
    largerText: bool = False
    captions: bool = False


class SurveyAnswers(BaseModel):
    level: Literal["new", "basics", "projects"]
    interests: list[Interest] = Field(default_factory=list, max_length=9)
    movieGenre: Literal["action", "comedy", "thriller", "romance", "sci-fi", "animation"] | None = None
    style: Literal["example", "diagram", "code"]
    language: Literal["tanglish", "english"]
    pace: Literal["calm", "balanced", "fast"]
    accessibility: Accessibility = Field(default_factory=Accessibility)


class SurveyOut(BaseModel):
    course_id: str
    answers: SurveyAnswers
    completed_at: str


def _out(row: CourseSurvey) -> SurveyOut:
    return SurveyOut(
        course_id=row.course_id,
        answers=SurveyAnswers.model_validate(row.answers),
        completed_at=(row.updated_at or row.completed_at or datetime.utcnow()).isoformat(),
    )


@router.get("", response_model=list[SurveyOut])
async def list_surveys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SurveyOut]:
    result = await db.execute(select(CourseSurvey).where(CourseSurvey.user_id == current_user.id))
    return [_out(row) for row in result.scalars().all()]


@router.put("/{course_id}", response_model=SurveyOut)
async def save_survey(
    course_id: str,
    body: SurveyAnswers,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SurveyOut:
    if await db.get(Course, course_id) is None:
        raise HTTPException(status_code=404, detail=f"Course {course_id!r} not found")
    answers = body.model_dump()
    # Genre only applies to movie lovers; drop stale picks.
    if "movies" not in answers["interests"]:
        answers["movieGenre"] = None
    answers["interests"] = list(dict.fromkeys(answers["interests"]))

    result = await db.execute(
        select(CourseSurvey).where(
            CourseSurvey.user_id == current_user.id, CourseSurvey.course_id == course_id
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = CourseSurvey(user_id=current_user.id, course_id=course_id, answers=answers)
        db.add(row)
    else:
        row.answers = answers
        row.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(row)
    return _out(row)


@router.delete("/{course_id}", status_code=204, response_class=Response)
async def delete_survey(
    course_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    result = await db.execute(
        select(CourseSurvey).where(
            CourseSurvey.user_id == current_user.id, CourseSurvey.course_id == course_id
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()
    return Response(status_code=204)
