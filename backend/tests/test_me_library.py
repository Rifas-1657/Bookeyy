"""
tests/test_me_library.py — /me/surveys, plus page_number and limits on
/me/bookmarks and /me/notes.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

ANSWERS = {
    "level": "basics",
    "interests": ["cricket", "cricket"],
    "movieGenre": "action",
    "style": "diagram",
    "language": "english",
    "pace": "fast",
    "accessibility": {"reducedMotion": True, "largerText": False, "captions": True},
}


async def test_survey_requires_auth(http_client: AsyncClient):
    assert (await http_client.get("/me/surveys")).status_code == 401


async def test_survey_round_trip(auth_http_client: AsyncClient):
    resp = await auth_http_client.put("/me/surveys/python", json=ANSWERS)
    assert resp.status_code == 200, resp.text
    saved = resp.json()["answers"]
    assert saved["interests"] == ["cricket"]
    assert saved["movieGenre"] is None  # dropped: movies not chosen

    listed = (await auth_http_client.get("/me/surveys")).json()
    assert [s["course_id"] for s in listed] == ["python"]

    updated = await auth_http_client.put("/me/surveys/python", json={**ANSWERS, "pace": "calm"})
    assert updated.json()["answers"]["pace"] == "calm"
    assert len((await auth_http_client.get("/me/surveys")).json()) == 1

    assert (await auth_http_client.delete("/me/surveys/python")).status_code == 204
    assert (await auth_http_client.get("/me/surveys")).json() == []


async def test_survey_rejects_unsupported_language(auth_http_client: AsyncClient):
    resp = await auth_http_client.put("/me/surveys/python", json={**ANSWERS, "language": "tamil"})
    assert resp.status_code == 422


async def test_survey_unknown_course(auth_http_client: AsyncClient):
    resp = await auth_http_client.put("/me/surveys/not-a-course", json=ANSWERS)
    assert resp.status_code == 404


async def test_bookmark_includes_page_number(auth_http_client: AsyncClient):
    resp = await auth_http_client.post(
        "/me/bookmarks",
        json={"course_id": "python", "chapter_number": 1, "page_id": "lesson_python_1_3", "excerpt": "x"},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["page_number"] == 3
    listed = (await auth_http_client.get("/me/bookmarks")).json()
    assert any(b["id"] == created["id"] and b["page_number"] == 3 for b in listed)
    await auth_http_client.delete(f"/me/bookmarks/{created['id']}")


async def test_note_limits_and_page_number(auth_http_client: AsyncClient):
    too_long = await auth_http_client.post(
        "/me/notes",
        json={"course_id": "python", "chapter_number": 1, "page_id": "lesson_python_1_2", "text": "a" * 2001},
    )
    assert too_long.status_code == 422
    empty = await auth_http_client.post(
        "/me/notes",
        json={"course_id": "python", "chapter_number": 1, "page_id": "lesson_python_1_2", "text": ""},
    )
    assert empty.status_code == 422
    ok = await auth_http_client.post(
        "/me/notes",
        json={"course_id": "python", "chapter_number": 1, "page_id": "lesson_python_1_2", "text": "remember"},
    )
    assert ok.status_code == 201
    assert ok.json()["page_number"] == 2
    await auth_http_client.delete(f"/me/notes/{ok.json()['id']}")


async def test_complete_page_counts_once_and_validates(auth_http_client: AsyncClient):
    bad = await auth_http_client.post("/me/progress/python/not-a-page/complete")
    assert bad.status_code == 404
    wrong_course = await auth_http_client.post("/me/progress/java/lesson_python_1_1/complete")
    assert wrong_course.status_code == 404

    for _ in range(2):
        ok = await auth_http_client.post("/me/progress/python/lesson_python_1_1/complete")
        assert ok.status_code == 200
    summary = {s["course_id"]: s for s in (await auth_http_client.get("/me/progress")).json()}
    assert summary["python"]["completed_pages"] >= 1
    items = (await auth_http_client.get("/me/progress/python")).json()
    assert sum(1 for i in items if i["page_id"] == "lesson_python_1_1") == 1
