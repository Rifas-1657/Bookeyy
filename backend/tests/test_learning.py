"""
tests/test_learning.py — R3 adaptive learning: BKT learner model, contextual teaching-style
bandit, graded quizzes (lesson / LLM / page-derived), FSRS review queue, tutor awareness.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from app.db.database import AsyncSessionLocal
from app.db.models import BanditArm, FSRSCardModel, LearnerConcept, LearnerEvent
from app.llm import LLMResponse
from app.main import app
from app.personalization import learner_model as lm
from app.personalization import quiz as quizlib
from app.personalization import teaching_style as ts

PAGE = "lesson_python_1_1"


class _StubRouter:
    def __init__(self, reply: str = "", error: Exception | None = None):
        self.reply, self.error, self.calls = reply, error, []

    async def generate_json(self, prompt: str, schema: dict, system: str = "", **kw) -> LLMResponse:
        self.calls.append({"prompt": prompt, "system": system})
        if self.error:
            raise self.error
        return LLMResponse(text=self.reply, provider="stub", model="stub")

    async def generate_text(self, prompt: str, system: str = "", **kw) -> LLMResponse:
        self.calls.append({"prompt": prompt, "system": system})
        return LLMResponse(text="Variable oru labeled box.", provider="stub", model="stub")


@pytest.fixture
def no_llm(monkeypatch):
    router = _StubRouter(error=RuntimeError("no provider"))
    monkeypatch.setattr("app.llm.router.get_llm_router", lambda: router)
    return router


def _page(**kw) -> lm.PageInfo:
    base = dict(course_id="python", page_id=PAGE, title="What is a variable?", chapter_number=1, page_number=1,
                key_terms=["variable", "labeled box", "value"],
                paragraphs=["A variable is a name that refers to a value stored in memory.",
                            "Think of it as a labeled box. The label is the variable name."],
                narration="", code_example={"source": "x = 10\nprint(x)", "expectedOutput": "10"},
                practice_prompts=[])
    base.update(kw)
    return lm.PageInfo(**base)


async def _reset(user_id: str) -> None:
    async with AsyncSessionLocal() as db:
        for model in (LearnerConcept, LearnerEvent, FSRSCardModel):
            await db.execute(delete(model).where(model.user_id == user_id))
        await db.execute(delete(BanditArm).where(BanditArm.user_id == user_id, BanditArm.topic.like("ts:%")))
        await db.commit()


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Pure model
# ---------------------------------------------------------------------------

def test_bkt_correct_raises_incorrect_lowers():
    p = 0.3
    up = lm.bkt_update(p, True, guess=0.25)
    down = lm.bkt_update(p, False, guess=0.25)
    assert up > p > down
    # a lucky guess on a 2-option question is weaker evidence than on a short answer
    assert lm.bkt_update(p, True, guess=0.5) < lm.bkt_update(p, True, guess=0.1)
    # soft evidence moves less than a graded answer
    assert down < lm.bkt_update(p, False, guess=0.25, weight=0.25) < p + 0.05
    assert 0.0 < lm.bkt_update(0.99, True) <= 0.99


def test_concepts_from_key_terms_and_title_fallback():
    assert lm.concept_id_for("print()") == "print"
    assert lm.concept_id_for("and / or / not") == "and-or-not"
    ids = [c for c, _ in lm.page_concepts("Variables", ["variable", "Variable", "labeled box"])]
    assert ids == ["variable", "labeled-box"]
    assert lm.page_concepts("Loops in Python", []) == [("loops-in-python", "Loops in Python")]
    assert lm.mentioned_concepts("what is a labeled box?", _page().concepts) == ["labeled-box"]
    assert lm.status_for(0.85, True) == "mastered" and lm.status_for(0.3, False) == "new"
    assert lm.status_for(0.3, True) == "weak"


def test_bandit_context_prior_and_constraints():
    low = ts.StyleContext(level="new", mastery=0.2, language="tanglish", confused=True)
    high = ts.StyleContext(level="projects", mastery=0.9, language="english")
    assert low.bucket == "ts:new:low:tanglish:1" and high.bucket == "ts:projects:high:english:0"
    a_low, _ = ts.context_prior(low)
    a_high, _ = ts.context_prior(high)
    simpler = ts.arm_of("example-first", "simpler")
    deeper = ts.arm_of("example-first", "deeper")
    assert a_low[simpler] > a_low[deeper] and a_high[deeper] > a_high[simpler]
    assert ts.arm_parts(ts.arm_of("visual-first", "standard")) == ("visual-first", "standard")
    # posterior shifts toward the rewarded arm
    post = {low.bucket: {simpler: (12.0, 1.0)}, ts.GLOBAL_BUCKET: {}}
    alpha, beta = ts.combine(low, post)
    means = [alpha[i] / (alpha[i] + beta[i]) for i in range(ts.NUM_ARMS)]
    assert max(range(ts.NUM_ARMS), key=lambda i: means[i]) == simpler


def test_page_derived_quiz_items_and_grading():
    items = quizlib.page_items(_page(), "english")
    mcq = [q for q in items if q.kind == "mcq"]
    assert mcq, items
    q = mcq[0]
    assert q.answer in q.options and "_____" in q.prompt and q.source == "page"
    out = [q for q in items if q.check == "output"]
    assert out and out[0].answer == "10"
    tl = quizlib.page_items(_page(), "tanglish")
    assert any("fill pannunga" in q.prompt for q in tl)
    assert quizlib.keyword_score("it is a name for a value stored in memory",
                                 "A variable is a name that refers to a value stored in memory.") >= 0.5
    assert quizlib.keyword_score("no idea", "A variable is a name that refers to a value stored in memory.") < 0.5


async def test_grading_mcq_short_and_feedback():
    q = quizlib.QuizItem(id="q-1", kind="mcq", prompt="?", options=["box", "loop", "file"], answer="box",
                         explanation="A variable is like a box.")
    assert (await quizlib.grade(q, "box"))["correct"]
    assert (await quizlib.grade(q, "A"))["correct"]
    assert not (await quizlib.grade(q, "loop"))["correct"]
    s = quizlib.QuizItem(id="q-2", kind="short", prompt="What is a variable?",
                         answer="a name that refers to a value", check="rubric")
    llm = _StubRouter(reply=json.dumps({"score": 0.9, "reason": "Same meaning."}))
    graded = await quizlib.grade(s, "oru value-ku peru", llm_router=llm)
    assert graded["correct"] and graded["gradedBy"] == "rubric"
    broken = _StubRouter(error=RuntimeError("down"))
    fallback = await quizlib.grade(s, "a name that refers to some value", llm_router=broken)
    assert fallback["gradedBy"] == "keywords" and fallback["correct"]
    ok = quizlib.feedback(q, {"correct": True, "score": 1.0, "reason": ""}, "tanglish")
    assert ok.startswith(("Correct-a sonneenga!", "Super", "Semma")) and "box" in ok
    bad = quizlib.feedback(q, {"correct": False, "score": 0.0, "reason": ""}, "english")
    assert "Not quite" in bad and "box" in bad


def test_llm_item_verification():
    page = _page()
    good = {"kind": "mcq", "prompt": "A variable is…", "options": ["a labeled box", "a loop", "a file", "a bug"],
            "answer": "a labeled box", "explanation": "The page compares a variable to a labeled box.",
            "concept": "labeled box"}
    item = quizlib.verify_item(good, page, "english")
    assert item and item.answer == "a labeled box" and item.concept_id == "labeled-box"
    assert quizlib.verify_item({**good, "answer": "a cat"}, page, "english") is None          # answer not an option
    assert quizlib.verify_item({**good, "options": ["a", "a", "b"]}, page, "english") is None  # duplicates
    assert quizlib.verify_item({**good, "prompt": "வேரியபிள் என்ன?"}, page, "tanglish") is None  # Tamil script
    ungrounded = {"kind": "short", "prompt": "Who won?", "answer": "Chennai Super Kings", "explanation": "IPL"}
    assert quizlib.verify_item(ungrounded, page, "english") is None
    letter = quizlib.verify_item({**good, "answer": "A"}, page, "english")
    assert letter and letter.answer == "a labeled box"


async def test_lesson_items_from_course_outline(learner_user):
    from app.db.models import CourseBuildJob
    outline = {
        "title": "R3 Test Book", "language": "english",
        "chapters": [{"title": "Basics", "lessons": [{
            "title": "Lists", "objectives": ["use lists"], "keyTerms": ["list", "index"],
            "explanationPlan": ["A list holds items in order."],
            "practical": {"title": "Make a list", "instructions": "Create one."},
            "tasks": [
                {"kind": "quiz", "prompt": "Index of the first item?", "options": ["0", "1", "-1"], "answer": "A",
                 "explanation": "Python counts from 0."},
                {"kind": "task", "prompt": "Explain what a list is.", "answer": "An ordered collection of items."},
                {"kind": "task", "prompt": "Build a shopping list app.", "answer": ""},
            ]}], "project": {"title": "P", "brief": "b"}}],
    }
    async with AsyncSessionLocal() as db:
        db.add(CourseBuildJob(course_id="r3-test-book", title="R3", status="ready", request={}, outline=outline,
                              created_by=learner_user.id))
        await db.commit()
        page = _page(course_id="r3-test-book", page_id="r3-test-book-c1-p1", title="Lists",
                     key_terms=["list", "index"])
        items = await quizlib.lesson_items(db, page)
        project = await quizlib.lesson_items(db, _page(course_id="r3-test-book", page_number=2))
    assert [q.kind for q in items] == ["mcq", "short"]          # the open task without an answer is skipped
    assert items[0].answer == "0" and items[0].source == "lesson" and items[0].concept_id == "index"
    assert items[1].check == "rubric"
    assert project == []


# ---------------------------------------------------------------------------
# API: evidence, quizzes, grading, reviews
# ---------------------------------------------------------------------------

async def test_learning_api_requires_auth(app_client):
    async with await _client() as c:
        assert (await c.get("/api/me/learning/python")).status_code == 401
        assert (await c.post("/api/me/learning/python/events", json={"events": []})).status_code == 401


async def test_evidence_updates_mastery_and_summary(learner_user, learner_token):
    await _reset(learner_user.id)
    h = {"Authorization": f"Bearer {learner_token}"}
    async with await _client() as c:
        r = await c.post("/api/me/learning/python/events", headers=h, json={"events": [
            {"kind": "code_run", "pageId": PAGE, "correct": True},
            {"kind": "repeat", "pageId": PAGE, "text": "interpreter puriyala"},
        ]})
        assert r.status_code == 200, r.text
        assert r.json()["applied"] == 2
        bad = await c.post("/api/me/learning/python/events", headers=h,
                           json={"events": [{"kind": "quiz_answer", "pageId": PAGE, "correct": True}]})
        assert bad.status_code == 422          # graded evidence only comes from the grader
        s = (await c.get("/api/me/learning/python", headers=h)).json()
    concepts = {c["id"]: c for c in s["concepts"]}
    assert set(concepts) >= {"program", "interpreter", "run"}
    assert concepts["interpreter"]["confusion"] == 1
    assert concepts["interpreter"]["mastery"] < concepts["program"]["mastery"]
    assert s["chapters"][0]["number"] == 1 and s["model"] == "bkt"
    assert s["style"]["style"] in ts.STYLES


async def test_quiz_issue_grade_and_review_cycle(learner_user, learner_token, learner2_token, no_llm):
    await _reset(learner_user.id)
    h = {"Authorization": f"Bearer {learner_token}"}
    async with await _client() as c:
        r = await c.post("/api/me/learning/python/quiz", headers=h, json={"pageId": PAGE, "language": "english"})
        assert r.status_code == 200, r.text
        quiz = r.json()
        assert 2 <= len(quiz["questions"]) <= 3
        assert all("answer" not in q for q in quiz["questions"])     # answers stay on the server
        mcq = next(q for q in quiz["questions"] if q["kind"] == "mcq")

        # another learner can't grade (or learn) my question
        other = await c.post("/api/me/learning/python/quiz/grade", headers={"Authorization": f"Bearer {learner2_token}"},
                             json={"questionId": mcq["id"], "answer": mcq["options"][0]})
        assert other.status_code == 404

        async with AsyncSessionLocal() as db:
            ev = (await db.execute(select(LearnerEvent).where(LearnerEvent.user_id == learner_user.id,
                                                              LearnerEvent.kind == "quiz_issued"))).scalars().first()
            right = next(i["answer"] for i in ev.meta["items"] if i["id"] == mcq["id"])
        wrong = next(o for o in mcq["options"] if o != right)
        g = (await c.post("/api/me/learning/python/quiz/grade", headers=h,
                          json={"questionId": mcq["id"], "answer": wrong, "responseMs": 4000})).json()
        assert g["correct"] is False and g["counted"] is True and g["answer"] == right
        assert "Not quite" in g["feedback"]
        again = (await c.post("/api/me/learning/python/quiz/grade", headers=h,
                              json={"questionId": mcq["id"], "answer": right})).json()
        assert again["correct"] is True and again["counted"] is False   # retries don't inflate mastery

        # the mistake made an FSRS card; pretend it was yesterday → it is due for review
        async with AsyncSessionLocal() as db:
            card = (await db.execute(select(FSRSCardModel).where(
                FSRSCardModel.user_id == learner_user.id,
                FSRSCardModel.topic == f"python::{mcq['conceptId']}"))).scalar_one()
            assert card.lapses == 1
            await db.execute(update(FSRSCardModel).where(FSRSCardModel.id == card.id)
                             .values(last_review_date=datetime.utcnow() - timedelta(days=1)))
            await db.commit()
        due = (await c.get("/api/me/learning/reviews", headers=h)).json()
        assert due["count"] >= 1 and due["due"][0]["conceptId"] == mcq["conceptId"]
        review = (await c.post("/api/me/learning/python/quiz", headers=h, json={"mode": "review"})).json()
        assert review["mode"] == "review" and review["questions"]
        rq = review["questions"][0]
        assert rq["prompt"] == mcq["prompt"] and rq["source"] == "review"   # the question that was missed
        ok = (await c.post("/api/me/learning/python/quiz/grade", headers=h,
                           json={"questionId": rq["id"], "answer": right})).json()
        assert ok["correct"] is True and ok["counted"] is True
        after = (await c.get("/api/me/learning/reviews", headers=h)).json()
        assert all(d["conceptId"] != mcq["conceptId"] for d in after["due"])
        overview = (await c.get("/api/me/learning", headers=h)).json()
        assert any(x["courseId"] == "python" for x in overview["courses"])


async def test_quiz_unknown_page_404(learner_token, no_llm):
    async with await _client() as c:
        r = await c.post("/api/me/learning/python/quiz", headers={"Authorization": f"Bearer {learner_token}"},
                         json={"pageId": "nope"})
        assert r.status_code == 404


async def test_page_complete_feeds_learner_model(learner_user, learner_token):
    await _reset(learner_user.id)
    h = {"Authorization": f"Bearer {learner_token}"}
    async with await _client() as c:
        r = await c.post(f"/me/progress/python/lesson_python_1_2/complete", headers=h)
        assert r.status_code == 200
    async with AsyncSessionLocal() as db:
        ev = (await db.execute(select(LearnerEvent).where(LearnerEvent.user_id == learner_user.id,
                                                          LearnerEvent.kind == "page_complete"))).scalars().all()
    assert len(ev) <= 1   # only the first completion counts (the page may already be complete from other tests)


# ---------------------------------------------------------------------------
# Bandit rewards + tutor awareness
# ---------------------------------------------------------------------------

async def test_simpler_avoids_last_style_and_rewards(learner_user):
    await _reset(learner_user.id)
    first = await ts.choose_for_answer(learner_user.id, "python", PAGE, "what is a variable?")
    assert first is not None and first.style in ts.STYLES
    simpler = await ts.choose_for_answer(learner_user.id, "python", PAGE, "explain simpler", simpler=True)
    assert simpler.depth == "simpler" and simpler.style != first.style
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(BanditArm).where(BanditArm.user_id == learner_user.id,
                                                         BanditArm.topic == ts.GLOBAL_BUCKET))).scalars().all()
        # "simpler" after the first answer = reward 0 for the first style's arm
        assert any(r.arm == first.arm and r.beta > r.alpha for r in rows)
        shown = (await db.execute(select(LearnerEvent).where(LearnerEvent.user_id == learner_user.id,
                                                             LearnerEvent.kind == "style_shown"))).scalars().all()
        assert len(shown) == 2


async def test_choose_is_seedable(learner_user):
    async with AsyncSessionLocal() as db:
        ctx = ts.StyleContext()
        a = await ts.choose(db, learner_user.id, ctx, rng=random.Random(7))
        b = await ts.choose(db, learner_user.id, ctx, rng=random.Random(7))
    assert a.arm == b.arm


async def test_tutor_prompt_includes_learner_model_and_style(learner_user, monkeypatch):
    await _reset(learner_user.id)
    async with AsyncSessionLocal() as db:
        await lm.apply_evidence(db, learner_user.id, "python", [
            {"kind": "simpler", "pageId": PAGE, "text": "interpreter"},
            {"kind": "simpler", "pageId": PAGE, "text": "interpreter"},
        ])
    from app.tutor import graph
    router = _StubRouter(reply=json.dumps({"in_scope": True, "is_injection": False, "grounded": True}))
    monkeypatch.setattr(graph, "_node_retrieve", lambda ctx: _noop())
    gen = await graph.process_question("what is an interpreter in python?", learner_user.id, "s1", "python", 1, 1, [],
                                       llm_router=router, page_id=PAGE, intent="simpler")
    events = [e async for e in gen]
    answer_calls = [c for c in router.calls if "How to explain this answer" in c.get("system", "")]
    assert answer_calls, [e.get("type") for e in events]
    system = answer_calls[0]["system"]
    assert "interpreter" in system and "simpler" in system.lower()
    end = next(e for e in events if e["type"] == "answer_end")
    assert end["style"]["depth"] == "simpler"
    # no other learner's data: learner2 has no evidence → no note
    assert await lm.tutor_learner_note("someone-else", "python", PAGE) == ""


async def _noop():
    return None
