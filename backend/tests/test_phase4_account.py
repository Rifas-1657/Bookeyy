"""
tests/test_phase4_account.py — account name, email verification, notifications,
and retaking the survey rebuilding the same personal book.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.auth import router as auth_router
from app.auth.service import create_email_verify_token
from app.books import personal
from app.db.database import AsyncSessionLocal
from app.db.models import CourseSurvey, LearnerBook, Notification, Page, Progress, User

from tests.test_personal_book import ANSWERS, FakeRouter


async def _signup(http_client, monkeypatch) -> tuple[dict, list]:
    sent: list = []

    async def fake_send(to, name, token):
        sent.append((to, name, token))

    monkeypatch.setattr(auth_router, "send_email_verification", fake_send)
    email = f"v-{uuid.uuid4().hex[:8]}@example.com"
    r = await http_client.post("/auth/signup", json={"email": email, "password": "a-Strong-pass-123", "display_name": "Vee"})
    assert r.status_code == 201, r.text
    return r.json(), sent


@pytest.mark.asyncio
async def test_signup_sends_a_verify_link_and_the_link_verifies(http_client, monkeypatch):
    data, sent = await _signup(http_client, monkeypatch)
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    assert len(sent) == 1 and sent[0][0] == data["email"]

    me = (await http_client.get("/auth/me", headers=headers)).json()
    assert me["email_verified"] is False

    r = await http_client.post("/auth/verify-email", json={"token": sent[0][2]})
    assert r.status_code == 200, r.text
    assert r.json()["email_verified"] is True
    assert (await http_client.get("/auth/me", headers=headers)).json()["email_verified"] is True


@pytest.mark.asyncio
async def test_verify_rejects_access_tokens_and_old_addresses(http_client, monkeypatch):
    data, _ = await _signup(http_client, monkeypatch)
    r = await http_client.post("/auth/verify-email", json={"token": data["access_token"]})
    assert r.status_code == 400
    stale = create_email_verify_token(data["user_id"], "someone-else@example.com")
    assert (await http_client.post("/auth/verify-email", json={"token": stale})).status_code == 400


@pytest.mark.asyncio
async def test_display_name_is_saved_to_the_account(http_client, monkeypatch):
    data, _ = await _signup(http_client, monkeypatch)
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    r = await http_client.patch("/auth/me", json={"display_name": "  Vee Kumar "}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Vee Kumar"
    assert (await http_client.patch("/auth/me", json={"display_name": "   "}, headers=headers)).status_code == 422
    async with AsyncSessionLocal() as db:
        assert (await db.get(User, data["user_id"])).display_name == "Vee Kumar"


@pytest.mark.asyncio
async def test_retake_rebuilds_the_same_book_and_keeps_progress(app_client, http_client, monkeypatch):
    monkeypatch.setattr(personal, "_writers", lambda: [FakeRouter()])
    async with AsyncSessionLocal() as db:
        user = User(email=f"r-{uuid.uuid4().hex[:8]}@example.com", display_name="Ravi", role="learner", password_hash="x")
        db.add(user)
        await db.flush()
        db.add(CourseSurvey(user_id=user.id, course_id="python", answers=ANSWERS))
        await db.commit()
        await db.refresh(user)

    first = await personal.start(user)
    await personal.build(first["jobId"], user.id)
    course_id = first["courseId"]
    page_id = f"{course_id}-c1-p1"
    async with AsyncSessionLocal() as db:
        db.add(Progress(user_id=user.id, course_id=course_id, page_id=page_id, completed=True))
        survey = (await db.execute(select(CourseSurvey).where(CourseSurvey.user_id == user.id))).scalar_one()
        survey.answers = {**ANSWERS, "level": "new", "interests": ["music"]}
        await db.commit()

    second = await personal.start(user)
    assert second["courseId"] == course_id and second["jobId"] != first["jobId"]
    await personal.build(second["jobId"], user.id)

    state = await personal.latest(user)
    assert state["ready"] and state["courseId"] == course_id and state["jobId"] == second["jobId"]
    want = personal.select_chapters({**ANSWERS, "level": "new"})
    async with AsyncSessionLocal() as db:
        pages = list((await db.execute(select(Page).where(Page.course_id == course_id))).scalars())
        kept = (await db.execute(select(Progress).where(Progress.user_id == user.id, Progress.page_id == page_id))).scalar_one()
        books = (await db.execute(select(LearnerBook).where(LearnerBook.owner_id == user.id))).scalars().all()
        ready = (await db.execute(select(Notification).where(
            Notification.user_id == user.id, Notification.kind == "book_ready"))).scalars().all()
    assert len(pages) == sum(len(c[5]) for c in want)
    assert kept.completed is True
    assert len(books) == 1
    assert len(ready) == 2


@pytest.mark.asyncio
async def test_notifications_list_links_and_delete(http_client, monkeypatch):
    data, _ = await _signup(http_client, monkeypatch)
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    async with AsyncSessionLocal() as db:
        db.add(Notification(user_id=data["user_id"], kind="book_ready", title="Your book is ready", body="Open it."))
        await db.commit()
    items = (await http_client.get("/me/notifications", headers=headers)).json()
    assert [i["href"] for i in items] == ["/courses/python"]
    r = await http_client.delete(f"/me/notifications/{items[0]['id']}", headers=headers)
    assert r.status_code == 204
    assert (await http_client.get("/me/notifications", headers=headers)).json() == []


@pytest.mark.asyncio
async def test_due_reviews_raise_one_notification_a_day(http_client, monkeypatch):
    from app.personalization import learner_model

    async def due(db, user_id, course_id=None):
        return [{"courseId": "python", "courseTitle": "Python", "conceptId": "loops", "label": "Loops"}]

    monkeypatch.setattr(learner_model, "due_reviews", due)
    data, _ = await _signup(http_client, monkeypatch)
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    await http_client.get("/me/notifications", headers=headers)
    items = (await http_client.get("/me/notifications", headers=headers)).json()
    assert [i["kind"] for i in items] == ["review_due"]
    assert items[0]["title"] == "1 quick review due" and items[0]["href"] == "/progress"
