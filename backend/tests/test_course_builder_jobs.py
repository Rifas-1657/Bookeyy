"""Course builder — outline, job pipeline and admin API (mock LLM, mock embeddings, mocked HTTP)."""
from __future__ import annotations

import tempfile

import httpx
import pytest
import pytest_asyncio
import respx

import app.knowledge as knowledge
import app.knowledge.sources.ingest as ingest_mod
import app.knowledge.worker as worker_mod
from app.course_builder import runner
from app.course_builder.outline import ChunkRef, SourceContext, heuristic_outline, parse_syllabus
from app.course_builder.schema import CourseOutline
from app.knowledge.embeddings.mock import MockEmbeddingBackend
from app.knowledge.storage.local import LocalStorageBackend
from app.knowledge.vectorstore.chroma import ChromaVectorStore
from app.llm.providers.mock import MockLLMProvider
from app.llm.router import LLMRouter

from tests.test_course_builder_sources import _mock_github

BASE = "/api/admin/course-builder"


@pytest.fixture(autouse=True)
def isolated_backends(monkeypatch):
    """Mock embeddings + in-memory chroma + temp storage, and a mock-only LLM router."""
    emb, vs, st = MockEmbeddingBackend(), ChromaVectorStore(persist_dir=None), LocalStorageBackend(root=tempfile.mkdtemp())
    for mod in (knowledge, worker_mod):
        monkeypatch.setattr(mod, "get_embedding_backend", lambda: emb)
        monkeypatch.setattr(mod, "get_vector_store", lambda: vs)
        monkeypatch.setattr(mod, "get_storage", lambda: st)
    monkeypatch.setattr(ingest_mod, "get_storage", lambda: st)
    monkeypatch.setattr(runner, "LLM_ROUTER_OVERRIDE", LLMRouter([MockLLMProvider(latency_ms=0)], retry_per_provider=0))
    yield


# ── pure outline logic ────────────────────────────────────────────────────────

SYLLABUS = """Week 1: Python basics
  - Variables and types
  - Input and output
Week 2: Control flow
  - if / else
  - Loops
"""


def test_parse_syllabus():
    plan = parse_syllabus(SYLLABUS)
    assert plan == [("Python basics", ["Variables and types", "Input and output"]),
                    ("Control flow", ["if / else", "Loops"])]


def test_heuristic_outline_is_deterministic_and_valid():
    src = SourceContext(0, "github", "acme/pycourse — overview", "https://github.com/acme/pycourse", "Python",
                        [ChunkRef("c1", "d1", 0, ["Intro"], "Python is a language. It is easy to read and write."),
                         ChunkRef("c2", "d1", 0, ["Loops"], "Loops repeat work. A for loop iterates a range.")])
    kw = dict(title="Py", description="d", level="beginner", language="english", target_audience="kids",
              syllabus_text=None, sources=[src], max_chapters=4, lessons_per_chapter=3)
    a, b = heuristic_outline(**kw), heuristic_outline(**kw)
    assert a.dump() == b.dump()
    lesson = a.chapters[0].lessons[0]
    assert lesson.id == "ch1-l1" and 2 <= len(lesson.tasks) <= 3
    assert lesson.practical.kind == "code" and lesson.practical.expected_output
    assert lesson.citations[0].chunk_id == "c1"
    assert a.chapters[0].project.title
    # round-trips through camelCase
    assert CourseOutline.model_validate(a.dump()).dump() == a.dump()


# ── API end-to-end ────────────────────────────────────────────────────────────

async def test_requires_admin(auth_http_client):
    r = await auth_http_client.get(f"{BASE}/jobs")
    assert r.status_code == 403


async def test_create_rejects_playlist(admin_http_client):
    r = await admin_http_client.post(f"{BASE}/jobs", json={
        "title": "Bad", "sources": [{"type": "youtube", "url": "https://www.youtube.com/playlist?list=PL1"}]})
    assert r.status_code == 422
    assert "playlist" in str(r.json()["detail"]).lower()


async def test_preview_github(admin_http_client):
    with respx.mock(assert_all_called=False) as router:
        _mock_github(router)
        r = await admin_http_client.post(f"{BASE}/preview-source", json={"url": "https://github.com/acme/pycourse"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "github" and body["title"] == "acme/pycourse" and "durationSeconds" in body


async def test_full_build_edit_generate_publish(admin_http_client, http_client, monkeypatch):
    monkeypatch.setenv("COURSE_BUILDER_AUTO_GENERATE_REMAINING", "false")
    with respx.mock(assert_all_called=False) as router:
        _mock_github(router)
        r = await admin_http_client.post(f"{BASE}/jobs", json={
            "title": "Python From Repos",
            "description": "Learn Python by reading a real course repo.",
            "level": "beginner",
            "language": "tanglish",
            "targetAudience": "college students",
            "sources": [
                {"type": "github", "url": "https://github.com/acme/pycourse"},
                {"type": "syllabus", "text": SYLLABUS},
            ],
            "syllabusText": SYLLABUS,
        })
        assert r.status_code == 201, r.text
        job_id = r.json()["jobId"]
        await runner.wait(job_id, timeout=120)

    r = await admin_http_client.get(f"{BASE}/jobs/{job_id}")
    job = r.json()
    assert job["status"] == "ready", job
    assert [s["status"] for s in job["sources"]] == ["indexed", "indexed"]
    assert job["sources"][0]["chunkCount"] > 0 and job["sources"][0]["documentIds"]
    steps = {s["key"]: s["status"] for s in job["steps"]}
    assert steps["ingest"] == steps["index"] == steps["outline"] == steps["lesson1"] == "done"
    assert steps["lessons"] == "pending"
    outline = job["outline"]
    assert outline["generator"] == "heuristic"          # mock LLM → deterministic outline
    assert [c["title"] for c in outline["chapters"]] == ["Python basics", "Control flow"]
    lessons = {l["id"]: l for l in job["lessons"]}
    assert lessons["ch1-l1"]["status"] == "done" and lessons["ch1-l2"]["status"] == "pending"
    assert 0 < job["progress"] < 100

    # SSE stream: job is terminal → snapshot + end event
    async with admin_http_client.stream("GET", f"{BASE}/jobs/{job_id}/stream") as resp:
        text = ""
        async for chunk in resp.aiter_text():
            text += chunk
    assert "event: job" in text and "event: end" in text

    # admin edits outline: rename a lesson in chapter 2 → only that lesson resets
    outline["chapters"][1]["lessons"][0]["title"] = "Conditionals"
    r = await admin_http_client.patch(f"{BASE}/jobs/{job_id}/outline", json=outline)
    assert r.status_code == 200, r.text
    assert r.json()["outline"]["generator"] == "admin"
    assert {l["id"]: l["status"] for l in r.json()["lessons"]}["ch1-l1"] == "done"

    bad = {"title": "x", "chapters": []}
    r = await admin_http_client.patch(f"{BASE}/jobs/{job_id}/outline", json=bad)
    assert r.status_code == 422

    # generate the rest
    r = await admin_http_client.post(f"{BASE}/jobs/{job_id}/generate", json={})
    assert r.status_code == 202 and "ch2-l2" in r.json()["queued"]
    await runner.wait(job_id)
    job = (await admin_http_client.get(f"{BASE}/jobs/{job_id}")).json()
    assert all(l["status"] == "done" for l in job["lessons"]), job["lessons"]
    assert job["progress"] == 100

    # publish → appears in public catalog
    r = await admin_http_client.post(f"{BASE}/jobs/{job_id}/publish", json={})
    assert r.status_code == 200, r.text
    info = r.json()
    course_id = info["courseId"]
    assert info["generatedPages"] == 4 and info["projectPages"] == 2 and info["pages"] == 6

    courses = (await http_client.get("/api/courses")).json()
    assert any(c["id"] == course_id and c["chapter_count"] == 2 for c in courses)
    pages = (await http_client.get(f"/api/courses/{course_id}/pages")).json()
    assert len(pages) == 6
    p1 = pages[0]
    assert p1["narration_text"] and p1["diagram_spec"]["nodes"] and p1["code_example"]["source"]
    assert any(pp.startswith("Quiz:") for pp in p1["practice_prompts"])
    assert pages[2]["title"].lower().startswith(("project", "mini project"))

    # re-publish is idempotent (upsert)
    r = await admin_http_client.post(f"{BASE}/jobs/{job_id}/publish", json={})
    assert r.status_code == 200
    assert len((await http_client.get(f"/api/courses/{course_id}/pages")).json()) == 6

    listing = (await admin_http_client.get(f"{BASE}/jobs")).json()
    assert any(j["id"] == job_id and j["publishedAt"] for j in listing)


async def test_all_sources_failing_fails_job(admin_http_client):
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.github.com/repos/acme/missing").mock(return_value=httpx.Response(404))
        r = await admin_http_client.post(f"{BASE}/jobs", json={
            "title": "Broken", "sources": [{"type": "github", "url": "https://github.com/acme/missing"}]})
        job_id = r.json()["jobId"]
        await runner.wait(job_id)
    job = (await admin_http_client.get(f"{BASE}/jobs/{job_id}")).json()
    assert job["status"] == "failed"
    assert "not found" in job["sources"][0]["error"].lower()
