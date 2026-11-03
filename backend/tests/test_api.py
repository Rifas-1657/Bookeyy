"""
tests/test_api.py — REST endpoint tests (B1).

Uses the `http_client` fixture from conftest (which depends on app_client,
ensuring the DB lifespan has run before any request is made).
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


pytestmark = pytest.mark.asyncio


async def test_health(http_client: AsyncClient):
    resp = await http_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    # B6 /health returns {"status", "service"} — no version/protocol keys


async def test_list_courses(http_client: AsyncClient):
    resp = await http_client.get("/api/courses")
    assert resp.status_code == 200
    courses = resp.json()
    assert len(courses) == 1
    assert courses[0]["id"] == "python"


async def test_list_pages_in_reading_order(http_client: AsyncClient):
    resp = await http_client.get("/api/courses/python/pages")
    assert resp.status_code == 200
    pages = resp.json()
    assert len(pages) > 0
    order = [(p["chapter_number"], p["page_number"]) for p in pages]
    assert order == sorted(order)
    assert pages[0]["id"] == "lesson_python_1_1"
    assert "expectedOutput" in pages[0]["code_example"]


async def test_list_pages_unknown_course(http_client: AsyncClient):
    resp = await http_client.get("/api/courses/not-a-course/pages")
    assert resp.status_code == 404
