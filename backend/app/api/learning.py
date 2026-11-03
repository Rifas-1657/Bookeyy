"""
app/api/learning.py — R3 adaptive learning endpoints (all per signed-in user; never other users' data).

GET  /api/me/learning                         courses with learner-model data (overview)
GET  /api/me/learning/reviews                 due spaced-review concepts across courses
GET  /api/me/learning/{course_id}             learner model summary for one course
POST /api/me/learning/{course_id}/events      batched evidence {events: [...]}
POST /api/me/learning/{course_id}/quiz        issue a graded quiz {pageId, n?, mode: page|review}
POST /api/me/learning/{course_id}/quiz/grade  grade one answer {questionId, answer, responseMs?}

Models: app/personalization/learner_model.py (BKT), teaching_style.py (contextual bandit),
quiz.py (sourcing + grading), fsrs.py (spaced review).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.books.access import ensure_course_visible
from app.db.database import get_db
from app.db.models import Course, User
from app.personalization import learner_model as lm
from app.personalization import quiz as quizlib

router = APIRouter(prefix="/api/me/learning", tags=["learning"])


async def _course(db: AsyncSession, course_id: str, user: User) -> Course:
    course = await db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail=f"Course {course_id!r} not found")
    await ensure_course_visible(db, course_id, user)
    return course


class EvidenceEvent(BaseModel):
    kind: Literal["code_run", "simpler", "question", "repeat", "relisten", "page_complete"]
    pageId: str = Field(min_length=1, max_length=80)
    conceptIds: list[str] = Field(default_factory=list, max_length=6)
    correct: bool | None = None
    responseMs: int | None = Field(default=None, ge=0, le=3_600_000)
    text: str = Field(default="", max_length=500)


class EvidenceBatch(BaseModel):
    events: list[EvidenceEvent] = Field(min_length=1, max_length=50)


class QuizRequest(BaseModel):
    pageId: str | None = Field(default=None, max_length=80)
    n: int = Field(default=3, ge=2, le=3)
    mode: Literal["page", "review"] = "page"
    language: Literal["tanglish", "english"] | None = None


class GradeRequest(BaseModel):
    questionId: str = Field(min_length=1, max_length=40)
    answer: str = Field(default="", max_length=500)
    responseMs: int | None = Field(default=None, ge=0, le=3_600_000)
    language: Literal["tanglish", "english"] | None = None


@router.get("")
async def learning_overview(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return {"courses": await lm.overview(db, user.id)}


@router.get("/reviews")
async def learning_reviews(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    due = await lm.due_reviews(db, user.id)
    return {"due": due, "count": len(due)}


@router.get("/{course_id}")
async def learning_summary(course_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _course(db, course_id, user)
    return await lm.course_summary(db, user.id, course_id)


@router.post("/{course_id}/events")
async def post_evidence(course_id: str, body: EvidenceBatch, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    await _course(db, course_id, user)
    updated = await lm.apply_evidence(db, user.id, course_id, [e.model_dump() for e in body.events])
    return {"applied": len(body.events), "concepts": updated}


@router.post("/{course_id}/quiz")
async def create_quiz(course_id: str, body: QuizRequest, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    await _course(db, course_id, user)
    prefs = await lm.learner_preferences(db, user.id, course_id)
    language = body.language or prefs["language"]
    if body.mode == "review":
        items = await _review_items(db, user.id, course_id, language)
        if not items:
            return {"quizId": None, "questions": [], "mode": "review"}
        quiz_id = await quizlib.issue(db, user.id, course_id, None, items, review=True, language=language)
        return {"quizId": quiz_id, "questions": [q.public() for q in items], "mode": "review"}
    if not body.pageId:
        raise HTTPException(status_code=422, detail="pageId is required")
    page = await lm.load_page_info(db, course_id, body.pageId)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found in this course")
    await lm.ensure_concepts(db, user.id, page, prefs["level"])
    await db.commit()
    items = await quizlib.build_quiz(db, user.id, page, language, body.n)
    if not items:
        return {"quizId": None, "questions": [], "mode": "page"}
    quiz_id = await quizlib.issue(db, user.id, course_id, page.page_id, items, language=language)
    return {"quizId": quiz_id, "questions": [q.public() for q in items], "mode": "page"}


async def _review_items(db, user_id: str, course_id: str, language: str) -> list[quizlib.QuizItem]:
    """Up to 3 questions on due concepts: the question that was missed, else a fresh page item."""
    from sqlalchemy import select
    from app.db.models import LearnerEvent
    due = (await lm.due_reviews(db, user_id, course_id))[:3]
    if not due:
        return []
    missed = (await db.execute(select(LearnerEvent).where(
        LearnerEvent.user_id == user_id, LearnerEvent.course_id == course_id,
        LearnerEvent.kind.in_(["quiz_answer", "review_answer"]), LearnerEvent.correct.is_(False),
    ).order_by(LearnerEvent.created_at.desc()).limit(50))).scalars().all()
    out: list[quizlib.QuizItem] = []
    for d in due:
        item = None
        for ev in missed:
            raw = (ev.meta or {}).get("item")
            if raw and raw.get("concept_id") == d["conceptId"]:
                item = quizlib.QuizItem(**{k: v for k, v in raw.items() if k in quizlib.QuizItem.__dataclass_fields__})
                break
        if item is None and d.get("pageId"):
            page = await lm.load_page_info(db, course_id, d["pageId"])
            if page is not None:
                cands = quizlib.page_items(page, language, only={d["conceptId"]})
                item = cands[0] if cands else None
        if item is not None:
            item.id = quizlib._qid()
            item.source = "review"
            out.append(item)
    return out


@router.post("/{course_id}/quiz/grade")
async def grade_answer(course_id: str, body: GradeRequest, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    await _course(db, course_id, user)
    found = await quizlib.find_issued(db, user.id, course_id, body.questionId)
    if found is None:
        raise HTTPException(status_code=404, detail="Question not found (it may have expired). Start a new quiz.")
    event, item, review = found
    prefs = await lm.learner_preferences(db, user.id, course_id)
    language = body.language or (event.meta or {}).get("language") or prefs["language"]
    result = await quizlib.grade(item, body.answer)
    meta = dict(event.meta or {})
    answered = list(meta.get("answered") or [])
    counted = item.id not in answered
    concepts: list[dict] = []
    if counted:
        meta["answered"] = answered + [item.id]
        event.meta = meta
        await db.flush()
        concepts = await lm.apply_evidence(db, user.id, course_id, [{
            "kind": "review_answer" if review else "quiz_answer",
            "pageId": item.page_id, "conceptIds": [item.concept_id], "correct": result["correct"],
            "responseMs": body.responseMs, "guess": item.guess,
            "meta": {"questionId": item.id, "score": result["score"], "source": item.source,
                     "item": None if result["correct"] else _stored(item)},
        }], level=prefs["level"])
    else:
        await db.commit()
    return {
        "questionId": item.id, "correct": result["correct"], "score": result["score"],
        "gradedBy": result["gradedBy"], "feedback": quizlib.feedback(item, result, language),
        "answer": item.answer, "explanation": item.explanation, "conceptId": item.concept_id,
        "conceptLabel": item.concept_label, "counted": counted,
        "mastery": next((c["mastery"] for c in concepts if c["id"] == item.concept_id), None),
    }


def _stored(item: quizlib.QuizItem) -> dict:
    from dataclasses import asdict
    return asdict(item)
