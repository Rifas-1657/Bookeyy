"""Durable job queue (app/jobs) + resumable course builds.

Runs on SQLite by default and on PostgreSQL with TEST_DATABASE_URL (see conftest.py):
claim exclusivity, lease-expiry requeue, backoff, max attempts / poison jobs, dedupe,
runner resume after a simulated crash mid-lessons, retry endpoints, quota counting.
"""
from __future__ import annotations

import asyncio
import tempfile
import uuid
from datetime import timedelta

import pytest
import respx
from sqlalchemy import update

import app.knowledge as knowledge
import app.knowledge.sources.ingest as ingest_mod
import app.knowledge.worker as worker_mod
from app.config import get_settings
from app.course_builder import jobs as cb_jobs
from app.course_builder import runner
from app.db.database import AsyncSessionLocal
from app.db.models import CourseBuildJob, CourseBuildLesson, Job, utcnow
from app.jobs import queue
from app.jobs import worker as jw
from app.knowledge.embeddings.mock import MockEmbeddingBackend
from app.knowledge.sources import SourceError
from app.knowledge.storage.local import LocalStorageBackend
from app.knowledge.vectorstore.chroma import ChromaVectorStore
from app.llm.providers.mock import MockLLMProvider
from app.llm.router import LLMRouter

from tests.test_books_api import _client, _create_upload, _new_user, _retire_test_users  # noqa: F401
from tests.test_course_builder_sources import _mock_github


@pytest.fixture(autouse=True)
def isolated_backends(monkeypatch):
    emb, vs, st = MockEmbeddingBackend(), ChromaVectorStore(persist_dir=None), LocalStorageBackend(root=tempfile.mkdtemp())
    for mod in (knowledge, worker_mod):
        monkeypatch.setattr(mod, "get_embedding_backend", lambda: emb)
        monkeypatch.setattr(mod, "get_vector_store", lambda: vs)
        monkeypatch.setattr(mod, "get_storage", lambda: st)
    monkeypatch.setattr(ingest_mod, "get_storage", lambda: st)
    monkeypatch.setattr(runner, "LLM_ROUTER_OVERRIDE", LLMRouter([MockLLMProvider(latency_ms=0)], retry_per_provider=0))
    monkeypatch.setenv("BOOKS_MAX_CHAPTERS", "2")
    monkeypatch.setenv("BOOKS_LESSONS_PER_CHAPTER", "2")
    yield


def _kind() -> str:
    return f"test.{uuid.uuid4().hex[:8]}"


def _key() -> str:
    return f"test:{uuid.uuid4().hex}"


async def _row(job_id: str) -> Job:
    async with AsyncSessionLocal() as db:
        return await db.get(Job, job_id)


async def _expire_lease(job_id: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(update(Job).where(Job.id == job_id).values(lease_expires_at=utcnow() - timedelta(seconds=5)))
        await db.commit()


async def _finish(job_id: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(update(Job).where(Job.id == job_id).values(status="succeeded", locked_by=None))
        await db.commit()


# ── queue semantics ───────────────────────────────────────────────────────────

async def test_dedupe_returns_active_job(app_client):
    key, kind = _key(), _kind()
    a, created_a = await queue.enqueue(kind, {"n": 1}, dedupe_key=key)
    b, created_b = await queue.enqueue(kind, {"n": 2}, dedupe_key=key)
    assert created_a and not created_b and a == b
    # concurrent enqueue with the same key still yields one active job
    ids = await asyncio.gather(*[queue.enqueue(kind, {}, dedupe_key=key) for _ in range(5)])
    assert {i for i, _ in ids} == {a}
    await _finish(a)
    c, created_c = await queue.enqueue(kind, {"n": 3}, dedupe_key=key)
    assert created_c and c != a          # finished jobs do not block new ones
    await _finish(c)


async def test_claim_is_exclusive(app_client):
    kind = _kind()
    ids = {(await queue.enqueue(kind, {"i": i}))[0] for i in range(4)}
    claims = await asyncio.gather(*[queue.claim(f"w{i}", kinds=[kind]) for i in range(10)])
    got = [c for c in claims if c is not None]
    assert len(got) == 4 and {c.id for c in got} == ids        # each job claimed exactly once
    assert len({c.worker_id for c in got}) == 4
    assert all(c.attempts == 1 for c in got)
    assert await queue.claim("late", kinds=[kind]) is None
    for c in got:
        assert await queue.complete(c.id, c.worker_id)
        assert (await _row(c.id)).status == "succeeded"


async def test_lease_expiry_requeues_and_old_worker_loses_it(app_client):
    kind = _kind()
    jid, _ = await queue.enqueue(kind, {})
    first = await queue.claim("dead-worker", kinds=[kind], lease_seconds=30)
    assert first.id == jid
    assert await queue.claim("other", kinds=[kind]) is None          # leased → not claimable
    assert await queue.heartbeat(jid, "dead-worker")
    await _expire_lease(jid)
    second = await queue.claim("rescuer", kinds=[kind])
    assert second is not None and second.id == jid and second.attempts == 2
    # the original worker can no longer touch it
    assert not await queue.heartbeat(jid, "dead-worker")
    assert not await queue.complete(jid, "dead-worker")
    assert await queue.fail(jid, "dead-worker", "x") is None
    assert await queue.complete(jid, "rescuer")


async def test_backoff_and_retry_until_success(app_client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "jobs_backoff_base_seconds", 10.0)
    monkeypatch.setattr(s, "jobs_backoff_max_seconds", 25.0)
    assert [queue.backoff_seconds(n) for n in (1, 2, 3, 4)] == [10.0, 20.0, 25.0, 25.0]

    kind = _kind()
    jid, _ = await queue.enqueue(kind, {}, max_attempts=3)
    c = await queue.claim("w", kinds=[kind])
    before = utcnow()
    assert await queue.fail(c.id, "w", "transient") == "queued"
    row = await _row(jid)
    assert row.status == "queued" and row.attempts == 1 and row.error == "transient" and row.locked_by is None
    delay = (row.run_after - before).total_seconds()
    assert 9.0 <= delay <= 11.0
    assert await queue.claim("w", kinds=[kind]) is None              # not before run_after
    async with AsyncSessionLocal() as db:
        await db.execute(update(Job).where(Job.id == jid).values(run_after=utcnow()))
        await db.commit()
    c2 = await queue.claim("w", kinds=[kind])
    assert c2.attempts == 2
    assert await queue.complete(c2.id, "w")


async def test_max_attempts_then_failed_with_on_dead(app_client):
    kind, key = _kind(), _key()
    calls, dead = [], []

    async def boom(job):
        calls.append(job.attempts)
        raise RuntimeError(f"boom {job.attempts}")

    async def on_dead(job, error):
        dead.append((job.id, error))

    jw.register(kind, on_dead=on_dead)(boom)
    jid, _ = await queue.enqueue(kind, {}, dedupe_key=key, max_attempts=3)
    await jw.run_until_idle(dedupe_keys=[key], timeout=30)
    row = await _row(jid)
    assert calls == [1, 2, 3]
    assert row.status == "failed" and row.attempts == 3 and "boom 3" in row.error and row.finished_at
    assert dead == [(jid, "RuntimeError: boom 3")]


async def test_permanent_error_skips_retries(app_client):
    kind, key = _kind(), _key()

    async def nope(job):
        raise jw.PermanentJobError("bad payload")

    jw.register(kind)(nope)
    jid, _ = await queue.enqueue(kind, {}, dedupe_key=key, max_attempts=5)
    await jw.run_until_idle(dedupe_keys=[key], timeout=30)
    row = await _row(jid)
    assert row.status == "failed" and row.attempts == 1


async def test_poison_job_fails_after_lease_expiries(app_client):
    """A job that keeps killing its worker (lease never completed) ends failed, not requeued forever."""
    kind = _kind()
    dead = []

    async def never(job):  # pragma: no cover - never reached
        raise AssertionError

    async def on_dead(job, error):
        dead.append(error)

    jw.register(kind, on_dead=on_dead)(never)
    jid, _ = await queue.enqueue(kind, {}, max_attempts=2)
    for n in (1, 2):
        c = await queue.claim(f"crash{n}", kinds=[kind])
        assert c is not None and c.attempts == n
        await _expire_lease(jid)
    assert await queue.claim("crash3", kinds=[kind]) is None          # no attempts left
    assert await jw.reap_once() >= 1
    row = await _row(jid)
    assert row.status == "failed" and "lease expired" in row.error
    assert len(dead) == 1


async def test_worker_loop_runs_jobs_and_heartbeats(app_client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "jobs_lease_seconds", 0.3)     # heartbeat every 0.1 s
    monkeypatch.setattr(s, "jobs_poll_interval", 0.05)
    kind = _kind()
    seen = []

    async def slow(job):
        await asyncio.sleep(0.6)                          # outlives the lease → must be renewed
        seen.append(job.payload["i"])
        return {"ok": job.payload["i"]}

    jw.register(kind)(slow)
    ids = [(await queue.enqueue(kind, {"i": i}))[0] for i in range(3)]
    w = jw.Worker(2, kinds=[kind])
    w.start()
    try:
        for _ in range(100):
            rows = [await _row(i) for i in ids]
            if all(r.status == "succeeded" for r in rows):
                break
            await asyncio.sleep(0.05)
    finally:
        await w.stop()
    rows = [await _row(i) for i in ids]
    assert all(r.status == "succeeded" and r.attempts == 1 for r in rows), [(r.status, r.attempts) for r in rows]
    assert sorted(seen) == [0, 1, 2]
    assert rows[0].job_meta == {"ok": 0}


async def test_worker_stop_releases_running_job(app_client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "jobs_poll_interval", 0.05)
    kind = _kind()
    started = asyncio.Event()

    async def forever(job):
        started.set()
        await asyncio.sleep(3600)

    jw.register(kind)(forever)
    jid, _ = await queue.enqueue(kind, {})
    w = jw.Worker(1, kinds=[kind])
    w.start()
    await asyncio.wait_for(started.wait(), 10)
    await w.stop(grace=0.1)
    row = await _row(jid)
    assert row.status == "queued" and row.attempts == 0 and row.locked_by is None
    await _finish(jid)


# ── resumable course build ────────────────────────────────────────────────────

async def test_build_resumes_after_crash_mid_lessons(app_client, monkeypatch):
    """Worker dies while generating lesson 3 of 4 → after the lease expires another worker
    resumes: no re-ingest, no new outline, lessons 1–2 kept, the book is re-published."""
    import app.course_builder.prompt_book as pb
    calls = {"lesson": 0, "outline": 0, "ingest": 0, "syllabus": 0}
    hang = asyncio.Event()
    real_lesson, real_outline, real_ingest, real_syl = (runner.generate_lesson_page, runner.generate_outline,
                                                        runner.ingest_source_document, pb.write_syllabus)

    async def lesson(**kw):
        calls["lesson"] += 1
        if calls["lesson"] == 3 and not hang.is_set():
            await asyncio.sleep(3600)                     # "crash" point
        return await real_lesson(**kw)

    async def outline(**kw):
        calls["outline"] += 1
        return await real_outline(**kw)

    async def ingest(*a, **kw):
        calls["ingest"] += 1
        return await real_ingest(*a, **kw)

    async def syllabus(*a, **kw):
        calls["syllabus"] += 1
        return await real_syl(*a, **kw)

    monkeypatch.setattr(runner, "generate_lesson_page", lesson)
    monkeypatch.setattr(runner, "generate_outline", outline)
    monkeypatch.setattr(runner, "ingest_source_document", ingest)
    monkeypatch.setattr(pb, "write_syllabus", syllabus)

    _, token = await _new_user()
    async with _client(token) as c:
        r = await c.post("/api/books", json={"prompt": "Teach me SQL joins for beginners"})
        assert r.status_code == 201, r.text
        job_id = r.json()["jobId"]
        key = runner.queue_key(job_id)
        assert (await c.get("/api/books/quota")).json()["running"] == 1          # queued counts

        claimed = await queue.claim("doomed", dedupe_keys=[key], lease_seconds=30)
        assert claimed is not None and claimed.kind == runner.RUN_KIND
        task = asyncio.ensure_future(cb_jobs.run_build(claimed))
        for _ in range(400):
            if calls["lesson"] >= 3:
                break
            await asyncio.sleep(0.05)
        assert calls == {"lesson": 3, "outline": 1, "ingest": 1, "syllabus": 1}
        task.cancel()                                     # the process dies; the queue row stays "running"
        await asyncio.gather(task, return_exceptions=True)

        mid = (await c.get(f"/api/books/jobs/{job_id}")).json()
        assert mid["busy"] is True and mid["bookReady"] is True
        assert [l["status"] for l in mid["lessons"]] == ["done", "done", "generating", "pending"]
        assert (await c.get("/api/books/quota")).json()["running"] == 1          # ready + busy counts

        await _expire_lease(claimed.id)
        hang.set()
        await runner.wait(job_id, timeout=120)

        job = (await c.get(f"/api/books/jobs/{job_id}")).json()
        q = (await c.get("/api/books/quota")).json()
    # no new syllabus / ingest / outline; only lessons 3 + 4 were generated again
    assert calls == {"lesson": 5, "outline": 1, "ingest": 1, "syllabus": 1}
    assert job["status"] == "ready" and job["busy"] is False, job
    assert all(l["status"] == "done" for l in job["lessons"])
    assert job["progress"] == 100
    steps = {s["key"]: s["status"] for s in job["steps"]}
    assert steps["lessons"] == "done" and steps["publish"] == "done"
    row = await _row(claimed.id)
    assert row.status == "succeeded" and row.attempts == 2
    assert q["running"] == 0


async def test_regenerate_is_idempotent_on_resume(app_client, admin_http_client, monkeypatch):
    monkeypatch.setenv("COURSE_BUILDER_AUTO_GENERATE_REMAINING", "true")
    with respx.mock(assert_all_called=False) as router:
        _mock_github(router)
        r = await admin_http_client.post("/api/admin/course-builder/jobs", json={
            "title": "Regen", "sources": [{"type": "github", "url": "https://github.com/acme/pycourse"}],
            "options": {"maxChapters": 1, "lessonsPerChapter": 2}})
        job_id = r.json()["jobId"]
        await runner.wait(job_id)
    calls = {"n": 0}
    real = runner.generate_lesson_page

    async def counting(**kw):
        calls["n"] += 1
        return await real(**kw)

    monkeypatch.setattr(runner, "generate_lesson_page", counting)
    r = await admin_http_client.post(f"/api/admin/course-builder/jobs/{job_id}/generate", json={"regenerate": True})
    assert r.status_code == 202, r.text
    # simulate: attempt 1 reset the lessons and regenerated one of them, then died
    claimed = await queue.claim("x", dedupe_keys=[runner.queue_key(job_id)])
    await runner.reset_lessons(job_id, None)
    await runner._generate_one(job_id, "ch1-l1")
    await _expire_lease(claimed.id)
    calls["n"] = 0
    await runner.wait(job_id)
    snap = (await admin_http_client.get(f"/api/admin/course-builder/jobs/{job_id}")).json()
    assert calls["n"] == 1                              # only the lesson still missing
    assert all(l["status"] == "done" for l in snap["lessons"])


# ── retry endpoints ───────────────────────────────────────────────────────────

async def test_books_retry_endpoint(app_client, monkeypatch):
    async def broken(type_, url):
        raise SourceError("upstream down")

    monkeypatch.setattr(runner, "fetch_source", broken)
    owner, token = await _new_user()
    _, other = await _new_user()
    async with _client(token) as c:
        r = await c.post("/api/books", json={"url": "https://github.com/acme/pycourse"})
        assert r.status_code == 201, r.text
        job_id = r.json()["jobId"]
        await runner.wait(job_id)
        job = (await c.get(f"/api/books/jobs/{job_id}")).json()
        assert job["status"] == "failed" and job["busy"] is False
        assert (await c.get("/api/books/quota")).json()["running"] == 0   # failed books never count

    async with _client(other) as c:
        assert (await c.post(f"/api/books/jobs/{job_id}/retry")).status_code == 404

    monkeypatch.undo()
    from app.knowledge.sources import fetch_source as real_fetch
    monkeypatch.setattr(runner, "fetch_source", real_fetch)
    async with _client(token) as c:
        r = await c.post(f"/api/books/jobs/{job_id}/retry")
        assert r.status_code == 202 and r.json() == {"jobId": job_id, "status": "queued"}
        assert (await c.post(f"/api/books/jobs/{job_id}/retry")).status_code == 409    # not failed any more
        snap = (await c.get(f"/api/books/jobs/{job_id}")).json()
        assert snap["status"] == "queued" and snap["busy"] is True and snap["error"] is None
        assert snap["sources"][0]["status"] == "pending"
        with respx.mock(assert_all_called=False) as router:
            _mock_github(router)
            await runner.wait(job_id, timeout=120)
        job = (await c.get(f"/api/books/jobs/{job_id}")).json()
    assert job["status"] == "ready", job
    assert job["sources"][0]["status"] == "indexed" and job["bookReady"] is True
    assert "Retried after a failure." in job["warnings"]


async def test_admin_retry_endpoint(admin_http_client, auth_http_client):
    base = "/api/admin/course-builder"
    with respx.mock(assert_all_called=False) as router:
        import httpx
        router.get("https://api.github.com/repos/acme/pycourse").mock(return_value=httpx.Response(404))
        r = await admin_http_client.post(f"{base}/jobs", json={
            "title": "Retry me", "sources": [{"type": "github", "url": "https://github.com/acme/pycourse"}]})
        job_id = r.json()["jobId"]
        await runner.wait(job_id)
    assert (await admin_http_client.get(f"{base}/jobs/{job_id}")).json()["status"] == "failed"
    assert (await auth_http_client.post(f"{base}/jobs/{job_id}/retry")).status_code == 403
    assert (await admin_http_client.post(f"{base}/jobs/nope/retry")).status_code == 404
    r = await admin_http_client.post(f"{base}/jobs/{job_id}/retry")
    assert r.status_code == 202
    with respx.mock(assert_all_called=False) as router:
        _mock_github(router)
        await runner.wait(job_id, timeout=120)
    job = (await admin_http_client.get(f"{base}/jobs/{job_id}")).json()
    assert job["status"] == "ready" and job["sources"][0]["status"] == "indexed"
    assert (await admin_http_client.post(f"{base}/jobs/{job_id}/retry")).status_code == 409


async def test_dead_build_job_marks_build_failed(app_client):
    """Queue job exhausted (e.g. repeated crashes) → the course build is failed and retryable."""
    owner, _ = await _new_user()
    async with AsyncSessionLocal() as db:
        job = CourseBuildJob(course_id=f"dead-{uuid.uuid4().hex[:6]}", title="Dead", created_by=owner.id,
                             status="generating", steps=[{"key": "lesson1", "status": "running", "detail": ""}])
        db.add(job)
        await db.flush()
        db.add(CourseBuildLesson(job_id=job.id, lesson_key="ch1-l1", chapter_number=1, lesson_number=1,
                                 title="L", content_hash="h", status="generating", attempts=[]))
        await db.commit()
        build_id = job.id
    jw.load_handlers()
    qid, _ = await queue.enqueue(runner.RUN_KIND, {"jobId": build_id}, dedupe_key=runner.queue_key(build_id),
                                 max_attempts=1)
    assert (await queue.claim("crashy", dedupe_keys=[runner.queue_key(build_id)])).id == qid
    await _expire_lease(qid)
    assert await jw.reap_once() >= 1
    async with AsyncSessionLocal() as db:
        build = await db.get(CourseBuildJob, build_id)
    assert build.status == "failed" and "lease expired" in build.error
    assert build.steps[0]["status"] == "failed"
    assert not await runner.is_busy(build_id)
