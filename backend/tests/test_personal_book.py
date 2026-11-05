"""
tests/test_personal_book.py — open signup and the survey-built Python book.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import func, select

from app.books import personal
from app.db.database import AsyncSessionLocal
from app.db.models import Chapter, Course, CourseSurvey, Page, User

ANSWERS = {"level": "basics", "interests": ["cricket"], "style": "code", "language": "english",
           "pace": "fast", "goal": "data", "dailyMinutes": "20", "project": "A score tracker"}


class FakeResponse:
    provider = "fake"

    def __init__(self, count: int) -> None:
        self.text = json.dumps({"lessons": [{"opening": "Think of your cricket scores.", "task": "Track runs."}] * count})


class FakeRouter:
    async def generate_json(self, *, prompt: str, **_kwargs) -> FakeResponse:
        return FakeResponse(prompt.count("\n", prompt.index("LESSONS:"), prompt.index("For EACH")) - 2)


def test_chapters_follow_level_and_goal():
    numbers = [c[0] for c in personal.select_chapters(ANSWERS)]
    assert 1 not in numbers and 4 in numbers          # basics skips the absolute beginning
    assert 16 in numbers and 15 not in numbers        # the data track only
    assert 19 in numbers                              # everyone ends with the capstone
    assert 1 in [c[0] for c in personal.select_chapters({"level": "new", "goal": "job"})]
    assert 4 not in [c[0] for c in personal.select_chapters({"level": "projects", "goal": "job"})]


def test_page_uses_language_opening_and_pace():
    lesson = personal.load_book()["9.1"]
    page = personal.page_fields(lesson, {"language": "english", "pace": "fast"}, opening="Hello there.", task="Do it.")
    assert page["narration_text"].startswith("Hello there. ") and lesson["narration_english"] in page["narration_text"]
    assert page["practice_prompts"][0] == "Do it." and page["practice_prompts"][-1].startswith("Quiz:")
    assert page["code_example"]["expectedOutput"] == lesson["expected_output"]
    tanglish = personal.page_fields(lesson, {"language": "tanglish"})
    assert tanglish["narration_text"] == lesson["narration_tanglish"]


@pytest.mark.asyncio
async def test_anyone_can_sign_up_as_a_learner(http_client):
    email = f"open-{uuid.uuid4().hex[:8]}@example.com"
    r = await http_client.post("/auth/signup", json={"email": email, "password": "a-Strong-pass-123", "display_name": "Open"})
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "learner"


@pytest.mark.asyncio
async def test_book_is_built_whole_from_the_survey(app_client, monkeypatch):
    monkeypatch.setattr(personal, "_writers", lambda: [FakeRouter()])
    async with AsyncSessionLocal() as db:
        user = User(email=f"p-{uuid.uuid4().hex[:8]}@example.com", display_name="Asha Kumar", role="learner", password_hash="x")
        db.add(user)
        await db.flush()
        db.add(CourseSurvey(user_id=user.id, course_id="python", answers=ANSWERS))
        await db.commit()
        await db.refresh(user)

    started = await personal.start(user)
    assert (await personal.latest(user))["ready"] is False
    await personal.build(started["jobId"], user.id)

    state = await personal.latest(user)
    assert state["ready"] and state["status"] == "ready"
    want = personal.select_chapters(ANSWERS)
    async with AsyncSessionLocal() as db:
        chapters = (await db.execute(select(func.count(Chapter.id)).where(Chapter.course_id == state["courseId"]))).scalar_one()
        pages = list((await db.execute(select(Page).where(Page.course_id == state["courseId"]))).scalars())
    assert chapters == len(want)
    assert len(pages) == sum(len(c[5]) for c in want)
    assert all(p.narration_text.startswith("Think of your cricket scores.") for p in pages)

    await personal.switch_language(user, "tanglish")
    async with AsyncSessionLocal() as db:
        first = await db.get(Page, f"{state['courseId']}-c1-p1")
        course = await db.get(Course, state["courseId"])
    assert first.narration_text == personal.load_book()["4.1"]["narration_tanglish"]
    assert course.language == "Tanglish"


@pytest.mark.asyncio
async def test_second_model_takes_over_when_the_first_is_full(monkeypatch):
    class Full:
        async def generate_json(self, **_kwargs):
            raise personal.LLMError("429", provider="groq", retryable=True)

    monkeypatch.setattr(personal, "_writers", lambda: [Full(), FakeRouter()])
    chapter = personal.select_chapters(ANSWERS)[0]
    texts = await personal._personal_text(chapter, ANSWERS, "Asha", deadline=personal.time.monotonic() + 5)
    assert len(texts) == len(chapter[5])
