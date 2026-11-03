"""P1 learner "Create a book" API — uploads, links, prompts, ownership, quota, SSE (mock LLM / embeddings / HTTP)."""
from __future__ import annotations

import io
import tempfile
import uuid

import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

import app.knowledge as knowledge
import app.knowledge.sources.ingest as ingest_mod
import app.knowledge.worker as worker_mod
from app.auth.service import create_access_token, hash_password
from app.course_builder import runner
from app.course_builder.prompt_book import AI_KNOWLEDGE_NOTE, heuristic_syllabus, topic_from_prompt
from app.db.database import AsyncSessionLocal
from app.db.models import User
from app.knowledge.embeddings.mock import MockEmbeddingBackend
from app.knowledge.storage.local import LocalStorageBackend
from app.knowledge.vectorstore.chroma import ChromaVectorStore
from app.llm.providers.mock import MockLLMProvider
from app.llm.router import LLMRouter
from app.main import app

from tests.test_course_builder_sources import _mock_github

SQL_NOTES = b"""# SQL joins

A join combines rows from two tables using a related column.

## Inner join

An INNER JOIN returns only the rows that have matching values in both tables.
For example, joining customers and orders on customer_id lists customers who placed orders.

## Left join

A LEFT JOIN returns every row from the left table and the matching rows from the right table.
Rows without a match get NULL values for the right table's columns.
"""


@pytest.fixture(autouse=True)
def isolated_backends(monkeypatch):
    emb, vs, st = MockEmbeddingBackend(), ChromaVectorStore(persist_dir=None), LocalStorageBackend(root=tempfile.mkdtemp())
    for mod in (knowledge, worker_mod):
        monkeypatch.setattr(mod, "get_embedding_backend", lambda: emb)
        monkeypatch.setattr(mod, "get_vector_store", lambda: vs)
        monkeypatch.setattr(mod, "get_storage", lambda: st)
    monkeypatch.setattr(ingest_mod, "get_storage", lambda: st)
    monkeypatch.setattr(runner, "LLM_ROUTER_OVERRIDE", LLMRouter([MockLLMProvider(latency_ms=0)], retry_per_provider=0))
    # small books keep the suite fast
    monkeypatch.setenv("BOOKS_MAX_CHAPTERS", "2")
    monkeypatch.setenv("BOOKS_LESSONS_PER_CHAPTER", "1")
    yield


_CREATED: list[str] = []


@pytest_asyncio.fixture(autouse=True)
async def _retire_test_users():
    """Test users are deactivated afterwards so seat-limit tests still see the platform's real counts."""
    yield
    if not _CREATED:
        return
    async with AsyncSessionLocal() as db:
        await db.execute(update(User).where(User.id.in_(list(_CREATED))).values(is_active=False))
        await db.commit()
    _CREATED.clear()


async def _new_user(role: str = "learner") -> tuple[User, str]:
    async with AsyncSessionLocal() as db:
        user = User(email=f"books-{uuid.uuid4().hex[:8]}@bookey.test", display_name="Book Maker", role=role,
                    password_hash=hash_password("pw-not-used-123"), is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    _CREATED.append(user.id)
    return user, create_access_token(user.id, user.role)


def _client(token: str | None = None) -> AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers)


async def _create_upload(c: AsyncClient, name: str = "sql-joins.md", data: bytes = SQL_NOTES, **fields) -> dict:
    r = await c.post("/api/books", files={"file": (name, io.BytesIO(data), "application/octet-stream")},
                     data={"level": "beginner", "language": "tanglish", **fields})
    assert r.status_code == 201, r.text
    return r.json()


# ── auth + validation ─────────────────────────────────────────────────────────

async def test_requires_auth(app_client):
    async with _client() as c:
        r = await c.post("/api/books", json={"prompt": "Teach me SQL joins"})
    assert r.status_code == 401


async def test_validation_errors(app_client):
    _, token = await _new_user()
    async with _client(token) as c:
        assert (await c.post("/api/books", json={})).status_code == 422
        assert (await c.post("/api/books", json={"prompt": "SQL", "level": "guru"})).status_code == 422
        r = await c.post("/api/books", json={"prompt": "how to make a bomb at home"})
        assert r.status_code == 422 and "can't" in r.json()["detail"]
        r = await c.post("/api/books", files={"file": ("virus.exe", io.BytesIO(b"MZ\x90\x00"), "application/octet-stream")})
        assert r.status_code == 415
        r = await c.post("/api/books", json={"url": "https://www.youtube.com/playlist?list=PL1"})
        assert r.status_code == 422 and "playlist" in r.json()["detail"].lower()


async def test_detect_endpoint(app_client):
    _, token = await _new_user()
    async with _client(token) as c:
        r = await c.post("/api/books/detect", json={"url": "youtu.be/dQw4w9WgXcQ"})
        assert r.status_code == 200 and r.json()["type"] == "youtube"
        r = await c.post("/api/books/detect", json={"url": "https://github.com/acme/pycourse"})
        assert r.json()["type"] == "github" and r.json()["label"] == "GitHub repository"
        r = await c.post("/api/books/detect", json={"url": "2401.01234"})
        assert r.json()["type"] == "arxiv"


def test_prompt_helpers():
    assert topic_from_prompt("Teach me SQL joins for beginners, in Tanglish") == "SQL joins"
    syl = heuristic_syllabus("Teach me photosynthesis")
    assert syl.generator == "heuristic" and "Chapter 1: Getting started with Photosynthesis" in syl.text


# ── upload → private book ─────────────────────────────────────────────────────

async def test_upload_book_is_private_to_owner(app_client):
    owner, token = await _new_user()
    _, other_token = await _new_user()
    _, admin_token = await _new_user("admin")
    async with _client(token) as c:
        created = await _create_upload(c)
        job_id, course_id = created["jobId"], created["courseId"]
        assert created["origin"] == "upload" and created["detectedType"] == "upload" and not created["aiKnowledge"]
        assert course_id.startswith("book-sql-joins-")
        await runner.wait(job_id, timeout=120)

        job = (await c.get(f"/api/books/jobs/{job_id}")).json()
        assert job["status"] == "ready", job
        assert job["bookReady"] is True and job["courseUrl"] == f"/book/{course_id}"
        assert job["sources"][0]["status"] == "indexed" and job["sources"][0]["chunkCount"] > 0
        steps = {s["key"]: s["status"] for s in job["steps"]}
        assert steps["lesson1"] == "done" and steps["publish"] == "done" and steps["lessons"] == "done"
        assert "syllabus" not in steps
        assert all(l["status"] == "done" for l in job["lessons"])

        # owner sees the book in the catalog + can read every page
        listing = (await c.get("/api/courses")).json()
        mine = next(x for x in listing if x["id"] == course_id)
        assert mine["visibility"] == "private" and mine["owner_id"] == owner.id and mine["origin"] == "upload"
        detail = (await c.get(f"/api/courses/{course_id}")).json()
        assert detail["category"] == "My books" and detail["build_job_id"] == job_id
        pages = (await c.get(f"/api/courses/{course_id}/pages")).json()
        assert len(pages) == detail["total_pages"] >= 2
        assert pages[0]["narration_text"] and pages[0]["diagram_spec"]["nodes"]
        one = await c.get(f"/api/courses/{course_id}/pages/{pages[0]['id']}")
        assert one.status_code == 200

        books = (await c.get("/api/books")).json()
        assert books[0]["jobId"] == job_id and books[0]["published"] is True

        # SSE: terminal job → one snapshot + end
        async with c.stream("GET", f"/api/books/jobs/{job_id}/stream") as resp:
            text = "".join([chunk async for chunk in resp.aiter_text()])
        assert "event: job" in text and "event: end" in text and '"bookReady": true' in text
        assert "syllabusText" not in text

    # everyone else: 404s, and the book is not listed
    for tok in (other_token, None):
        async with _client(tok) as c:
            assert all(x["id"] != course_id for x in (await c.get("/api/courses")).json())
            assert (await c.get(f"/api/courses/{course_id}")).status_code == 404
            assert (await c.get(f"/api/courses/{course_id}/pages")).status_code == 404
            assert (await c.get(f"/api/courses/{course_id}/pages/{pages[0]['id']}")).status_code == 404
    async with _client(other_token) as c:
        assert (await c.get(f"/api/books/jobs/{job_id}")).status_code == 404
        assert (await c.get(f"/api/books/jobs/{job_id}/stream")).status_code == 404

    # admins can open it (and list it with ?all=true)
    async with _client(admin_token) as c:
        assert (await c.get(f"/api/courses/{course_id}/pages")).status_code == 200
        assert (await c.get(f"/api/books/jobs/{job_id}")).status_code == 200
        assert all(x["id"] != course_id for x in (await c.get("/api/courses")).json())
        assert any(x["id"] == course_id for x in (await c.get("/api/courses?all=true")).json())


async def test_pptx_upload_builds_book(app_client):
    from tests.test_books_pptx_tutor import make_pptx
    _, token = await _new_user()
    async with _client(token) as c:
        created = await _create_upload(c, name="joins_deck.pptx", data=make_pptx(), language="english")
        await runner.wait(created["jobId"], timeout=120)
        job = (await c.get(f"/api/books/jobs/{created['jobId']}")).json()
    assert job["status"] == "ready", job
    assert job["sources"][0]["status"] == "indexed", job["sources"]
    assert created["title"] == "Joins deck"


# ── prompt-only book ──────────────────────────────────────────────────────────

async def test_prompt_book_is_labelled_ai_knowledge(app_client):
    _, token = await _new_user()
    async with _client(token) as c:
        r = await c.post("/api/books", json={"prompt": "Teach me SQL joins for beginners, in Tanglish",
                                             "language": "tanglish"})
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["origin"] == "prompt" and created["aiKnowledge"] is True and created["title"] == "SQL joins"
        await runner.wait(created["jobId"], timeout=120)
        job = (await c.get(f"/api/books/jobs/{created['jobId']}")).json()
        assert job["status"] == "ready", job
        assert job["steps"][0]["key"] == "syllabus" and job["steps"][0]["status"] == "done"
        assert AI_KNOWLEDGE_NOTE in job["warnings"]
        assert job["aiKnowledge"] is True and job["request"]["aiKnowledge"] is True
        assert job["sources"][0]["type"] == "syllabus" and job["sources"][0]["status"] == "indexed"
        assert job["outline"]["chapters"][0]["title"].startswith("Getting started with SQL joins")
        detail = (await c.get(f"/api/courses/{created['courseId']}")).json()
        assert detail["ai_generated"] is True and detail["subtitle"] == "Generated from AI knowledge"
        assert detail["language"] == "Tanglish"


# ── link book ─────────────────────────────────────────────────────────────────

async def test_github_link_book(app_client):
    _, token = await _new_user()
    async with _client(token) as c:
        with respx.mock(assert_all_called=False) as router:
            _mock_github(router)
            r = await c.post("/api/books", data={"url": "github.com/acme/pycourse", "level": "intermediate"})
            assert r.status_code == 201, r.text
            created = r.json()
            await runner.wait(created["jobId"], timeout=120)
        assert created["detectedType"] == "github" and created["origin"] == "url"
        assert created["title"] == "acme / pycourse"
        job = (await c.get(f"/api/books/jobs/{created['jobId']}")).json()
    assert job["status"] == "ready", job
    assert job["sources"][0]["type"] == "github" and job["sources"][0]["status"] == "indexed"


# ── quota ─────────────────────────────────────────────────────────────────────

async def test_daily_quota(app_client, monkeypatch):
    monkeypatch.setenv("BOOKS_MAX_PER_DAY", "1")
    _, token = await _new_user()
    async with _client(token) as c:
        first = await _create_upload(c)
        await runner.wait(first["jobId"], timeout=120)
        q = (await c.get("/api/books/quota")).json()
        assert q == {"running": 0, "maxConcurrent": 3, "today": 1, "maxPerDay": 1, "exempt": False}
        r = await c.post("/api/books", json={"prompt": "Teach me CSS grid"})
        assert r.status_code == 429 and "Daily limit" in r.json()["detail"]


async def test_concurrent_quota(app_client, monkeypatch):
    monkeypatch.setenv("BOOKS_MAX_CONCURRENT", "1")
    _, token = await _new_user()
    async with _client(token) as c:
        first = await _create_upload(c)
        try:
            r = await c.post("/api/books", json={"prompt": "Teach me CSS grid"})
            assert r.status_code == 429 and "at a time" in r.json()["detail"]
        finally:
            await runner.wait(first["jobId"], timeout=120)
        # once the first book is done a new one may start
        r = await c.post("/api/books", json={"prompt": "Teach me CSS grid"})
        assert r.status_code == 201
        await runner.wait(r.json()["jobId"], timeout=120)
