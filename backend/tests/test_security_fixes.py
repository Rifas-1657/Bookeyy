"""
tests/test_security_fixes.py — regression tests for the backend security audit fixes.

Covers: code-runner opt-in, SSRF (IP classes + per-hop redirect validation), voice auth +
provider override, rate limiting, tutor access before retrieval, interrupted-job recovery,
mock-LLM gating, zip bombs, YouTube subprocess env, reset-token logging, JWT revocation
(token_version), atomic invites, search privacy, naive-UTC expiry, body-size limits,
WS seek / audio caps / task cleanup, LLM router deadline, PDF page caps, progress access,
production settings validation, transliteration isolation.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import uuid
import zipfile
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.auth.service import create_access_token, hash_password
from app.config import Settings, get_settings, validate_production_settings
from app.db.database import AsyncSessionLocal
from app.db.models import Course, CourseBuildJob, CourseBuildLesson, Invite, LearnerBook, User, utcnow
from app.main import app

_CREATED: list[str] = []


@pytest.fixture(autouse=True)
async def _deactivate_created_users():
    yield
    if _CREATED:
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id.in_(list(_CREATED))).values(is_active=False))
            await db.commit()
        _CREATED.clear()


async def _new_user(role: str = "learner", password: str = "pw-sec-tests-123") -> tuple[User, str]:
    async with AsyncSessionLocal() as db:
        user = User(email=f"sec-{uuid.uuid4().hex[:10]}@example.com", display_name="Sec", role=role,
                    password_hash=hash_password(password), is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    _CREATED.append(user.id)
    return user, create_access_token(user.id, user.role, user.token_version or 0)


def _client(token: str | None = None) -> AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers)


# ── 1. Code execution is opt-in; learner books never execute ────────────────────

def _code_page(code: str, expected: str = "x"):
    from app.page_gen.schema import GeneratedPage
    from tests.test_page_gen import _valid_page_dict
    data = _valid_page_dict()
    for seg in data["segments"]:
        if seg.get("type") == "code":
            seg["code"], seg["expected_output"] = code, expected
            break
    else:
        pytest.skip("fixture page has no code segment")
    page = GeneratedPage.model_validate(data)
    page.narration = " ".join(["word"] * 80)
    page.word_count = 80
    return page


async def test_code_not_executed_when_runner_disabled(tmp_path, monkeypatch):
    from app.page_gen.verifier import verify_page
    monkeypatch.setattr(get_settings(), "code_runner_enabled", False)
    marker = tmp_path / "pwned.txt"
    page = _code_page(f"open({str(marker)!r}, 'w').write('x')\nprint('x')")
    vr = await verify_page(page)
    assert not marker.exists(), "code must never run when CODE_RUNNER_ENABLED is false"
    assert not any("Code segment" in e for e in vr.errors)


async def test_syntax_errors_still_caught_without_execution(monkeypatch):
    from app.page_gen.verifier import verify_page
    monkeypatch.setattr(get_settings(), "code_runner_enabled", False)
    vr = await verify_page(_code_page("def broken(:\n    pass"))
    assert any("SyntaxError" in e for e in vr.errors)


async def test_allow_execute_false_never_runs_even_if_enabled(tmp_path, monkeypatch):
    from app.page_gen.verifier import verify_page
    monkeypatch.setattr(get_settings(), "code_runner_enabled", True)
    marker = tmp_path / "pwned2.txt"
    await verify_page(_code_page(f"open({str(marker)!r}, 'w').write('x')\nprint('x')"), allow_execute=False)
    assert not marker.exists()


async def test_enabled_runner_is_isolated_from_secrets(monkeypatch):
    from app.page_gen.sandbox import run_code
    monkeypatch.setattr(get_settings(), "code_runner_enabled", True)
    monkeypatch.setenv("BOOKEY_SANDBOX_CANARY", "leak-me")
    actual, err = await run_code("import os\nprint(os.environ.get('BOOKEY_SANDBOX_CANARY', 'none'))", "none")
    assert err is None and actual == "none"


async def test_runner_disabled_by_default():
    assert Settings(app_env="development").code_runner_enabled is False


# ── 2. SSRF ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.5", "169.254.169.254", "100.64.0.1",
    "0.0.0.0", "::1", "::ffff:127.0.0.1", "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe",
    "fe80::1", "fc00::1", "2002:7f00:0001::1", "224.0.0.1", "198.18.0.1", "240.0.0.1",
])
def test_non_global_ips_blocked(ip):
    from app.knowledge.url_fetcher import _is_private_ip
    assert _is_private_ip(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_global_ips_allowed(ip):
    from app.knowledge.url_fetcher import _is_private_ip
    assert not _is_private_ip(ip)


def test_mapped_ip_literal_rejected_by_validate_url():
    from app.knowledge.url_fetcher import FetchError, validate_url
    with pytest.raises(FetchError):
        validate_url("http://[::ffff:169.254.169.254]/latest/meta-data/")


def _block_internal(hostname: str) -> None:
    from app.knowledge.url_fetcher import FetchError
    if hostname.startswith("internal"):
        raise FetchError(f"URL '{hostname}' resolves to a private or reserved IP address.")


async def test_fetch_url_validates_every_redirect_hop():
    from app.knowledge.url_fetcher import FetchError, fetch_url
    with respx.mock(assert_all_called=False) as router:
        router.get("https://public.example.com/doc.txt").mock(return_value=httpx.Response(
            302, headers={"location": "https://internal.example.com/secret"}))
        internal = router.get("https://internal.example.com/secret").mock(
            return_value=httpx.Response(200, text="SECRET", headers={"content-type": "text/plain"}))
        with patch("app.knowledge.url_fetcher._resolve_and_check", side_effect=_block_internal):
            with pytest.raises(FetchError, match="private"):
                await fetch_url("https://public.example.com/doc.txt")
        assert not internal.called, "the internal hop must be refused BEFORE it is requested"


async def test_fetch_url_redirect_to_metadata_ip_blocked():
    from app.knowledge.url_fetcher import FetchError, fetch_url
    with respx.mock(assert_all_called=False) as router:
        router.get("https://public.example.com/a").mock(return_value=httpx.Response(
            301, headers={"location": "http://169.254.169.254/latest/meta-data/"}))
        meta = router.get("http://169.254.169.254/latest/meta-data/").mock(return_value=httpx.Response(200))
        with patch("app.knowledge.url_fetcher._resolve_and_check"):
            with pytest.raises(FetchError):
                await fetch_url("https://public.example.com/a")
        assert not meta.called


async def test_fetch_url_follows_safe_redirects_and_caps_them():
    from app.knowledge.url_fetcher import FetchError, fetch_url
    with respx.mock(assert_all_called=False) as router:
        router.get("https://public.example.com/old").mock(return_value=httpx.Response(
            302, headers={"location": "/new.txt"}))
        router.get("https://public.example.com/new.txt").mock(return_value=httpx.Response(
            200, text="hello", headers={"content-type": "text/plain"}))
        router.get(url__regex=r"https://loop\.example\.com/.*").mock(return_value=httpx.Response(
            302, headers={"location": "https://loop.example.com/again"}))
        with patch("app.knowledge.url_fetcher._resolve_and_check"):
            res = await fetch_url("https://public.example.com/old")
            assert res.data == b"hello" and res.final_url.endswith("/new.txt")
            with pytest.raises(FetchError, match="redirects"):
                await fetch_url("https://loop.example.com/start")


async def test_web_preview_redirect_to_internal_blocked():
    from app.knowledge.sources.base import SourceError
    from app.knowledge.sources.web import preview_web
    with respx.mock(assert_all_called=False) as router:
        router.get("https://public.example.com/page").mock(return_value=httpx.Response(
            302, headers={"location": "https://internal.example.com/admin"}))
        internal = router.get("https://internal.example.com/admin").mock(return_value=httpx.Response(200))
        with patch("app.knowledge.url_fetcher._resolve_and_check", side_effect=_block_internal):
            with pytest.raises(SourceError):
                await preview_web("https://public.example.com/page")
        assert not internal.called


async def test_adapter_http_client_blocks_redirect_to_private_ip():
    from app.knowledge.sources.base import SourceError, http_client
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.github.com/repos/x/y").mock(return_value=httpx.Response(
            302, headers={"location": "http://10.0.0.1/internal"}))
        internal = router.get("http://10.0.0.1/internal").mock(return_value=httpx.Response(200))
        async with http_client() as client:
            with pytest.raises(SourceError):
                await client.get("https://api.github.com/repos/x/y")
        assert not internal.called


# ── 3. Voice: auth, provider override, rate limit ───────────────────────────────

async def test_voice_endpoints_require_auth(app_client):
    async with _client() as c:
        r1 = await c.post("/api/voice/tts", json={"text": "hello"})
        r2 = await c.post("/api/voice/stt", files={"file": ("a.wav", b"RIFF0000", "audio/wav")})
    assert r1.status_code == 401 and r2.status_code == 401


async def test_tts_provider_override_ignored_for_learners(app_client, monkeypatch):
    import app.api.voice as voice_api
    seen: list = []

    async def fake_local(req, fmt):
        from fastapi import Response
        seen.append(req.provider)
        return Response(content=b"x", media_type="audio/wav")

    monkeypatch.setattr(get_settings(), "tts_provider", "local")
    monkeypatch.setattr(voice_api, "_local_tts", fake_local)
    _, learner_tok = await _new_user("learner")
    _, admin_tok = await _new_user("admin")
    async with _client(learner_tok) as c:
        assert (await c.post("/api/voice/tts", json={"text": "hi", "provider": "gemini"})).status_code == 200
    async with _client(admin_tok) as c:
        assert (await c.post("/api/voice/tts", json={"text": "hi", "provider": "gemini"})).status_code == 200
    assert seen == [None, "gemini"]


# ── 4. Rate limiting ────────────────────────────────────────────────────────────

@pytest.fixture
def limiter_on(monkeypatch):
    from app.middleware.rate_limit import limiter
    limiter.reset()
    monkeypatch.setattr(limiter, "enabled", True)
    yield limiter
    limiter.reset()


async def test_login_is_rate_limited(app_client, limiter_on, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_auth_per_minute", 3)
    async with _client() as c:
        codes = [(await c.post("/auth/login", json={"email": "nobody@example.com", "password": "x"})).status_code
                 for _ in range(5)]
    assert codes[:3] == [401, 401, 401]
    assert codes[3:] == [429, 429]


async def test_default_limit_applies_to_all_routes_keyed_by_user(app_client, limiter_on, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 2)
    _, tok_a = await _new_user()
    _, tok_b = await _new_user()
    async with _client(tok_a) as a, _client(tok_b) as b:
        ra = [(await a.get("/me/progress")).status_code for _ in range(3)]
        rb = (await b.get("/me/progress")).status_code
    assert ra == [200, 200, 429]
    assert rb == 200, "limits are per user, not global"


def test_x_forwarded_for_only_trusted_from_configured_proxies(monkeypatch):
    from starlette.requests import Request
    from app.middleware.rate_limit import client_ip

    def req(peer: str) -> Request:
        return Request({"type": "http", "client": (peer, 1234), "headers": [(b"x-forwarded-for", b"9.9.9.9")],
                        "method": "GET", "path": "/"})

    monkeypatch.setattr(get_settings(), "trusted_proxies", "")
    assert client_ip(req("10.0.0.5")) == "10.0.0.5"
    monkeypatch.setattr(get_settings(), "trusted_proxies", "10.0.0.0/8")
    assert client_ip(req("10.0.0.5")) == "9.9.9.9"
    assert client_ip(req("8.8.4.4")) == "8.8.4.4"


# ── 5. Tutor: access check before retrieval, even without a Course row ──────────

async def test_tutor_refuses_unpublished_private_book_of_other_user(app_client):
    from app.tutor.graph import process_question
    owner, _ = await _new_user()
    other, _ = await _new_user()
    cid = f"lb-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        job = CourseBuildJob(course_id=cid, title="Private", created_by=owner.id, status="generating")
        db.add(job)
        await db.flush()
        db.add(LearnerBook(course_id=cid, job_id=job.id, owner_id=owner.id, visibility="private"))
        await db.commit()
        assert await db.get(Course, cid) is None

    with patch("app.tutor.graph._node_retrieve") as retrieve:
        events = [e async for e in await process_question(
            "what is this?", other.id, "s1", cid, 1, 1, [])]
        assert not retrieve.called
    assert events and events[0]["type"] == "scope_refused"
    assert "access" in events[0]["message"].lower()


async def test_access_for_unpublished_admin_job_without_learner_book(app_client):
    from app.books.access import user_can_view_course
    owner, _ = await _new_user("admin")
    other, _ = await _new_user()
    cid = f"cb-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        db.add(CourseBuildJob(course_id=cid, title="Draft", created_by=owner.id, status="outlining"))
        await db.commit()
    assert await user_can_view_course(cid, owner.id)
    assert not await user_can_view_course(cid, other.id)
    assert not await user_can_view_course(cid, None)


# ── 6. Orphaned builds (no queue job) are failed by the worker sweep ─────────────
# (Builds WITH a queue job are resumed instead — see tests/test_jobs_queue.py.)

async def test_orphaned_builds_failed_by_sweep(app_client):
    from app.course_builder import runner
    from app.course_builder.jobs import fail_orphaned_builds
    from app.jobs import queue
    owner, _ = await _new_user()
    old = utcnow() - timedelta(hours=2)
    async with AsyncSessionLocal() as db:
        stuck = CourseBuildJob(course_id=f"st-{uuid.uuid4().hex[:6]}", title="Stuck", created_by=owner.id,
                               status="generating", steps=[{"key": "lesson1", "status": "running", "detail": ""}],
                               updated_at=old)
        queued = CourseBuildJob(course_id=f"qd-{uuid.uuid4().hex[:6]}", title="Queued", created_by=owner.id,
                                status="queued", updated_at=old)
        done = CourseBuildJob(course_id=f"ok-{uuid.uuid4().hex[:6]}", title="Done", created_by=owner.id,
                              status="ready", updated_at=old)
        db.add_all([stuck, queued, done])
        await db.flush()
        db.add(CourseBuildLesson(job_id=stuck.id, lesson_key="c1l1", chapter_number=1, lesson_number=1,
                                 title="L", content_hash="h", status="generating", attempts=[]))
        await db.commit()
        stuck_id, queued_id, done_id = stuck.id, queued.id, done.id
    # `queued` has live queue work → it will resume, so it is not an orphan
    qid, _ = await queue.enqueue("test.noop", {}, dedupe_key=runner.queue_key(queued_id))
    try:
        assert await fail_orphaned_builds() >= 1
    finally:
        async with AsyncSessionLocal() as db:
            from app.db.models import Job
            row = await db.get(Job, qid)
            row.status = "succeeded"
            await db.commit()
    async with AsyncSessionLocal() as db:
        s = await db.get(CourseBuildJob, stuck_id)
        q = await db.get(CourseBuildJob, queued_id)
        d = await db.get(CourseBuildJob, done_id)
        lesson = (await db.execute(select(CourseBuildLesson).where(CourseBuildLesson.job_id == stuck_id))).scalar_one()
    assert s.status == "failed" and "restart" in s.error
    assert s.steps[0]["status"] == "failed"
    assert q.status == "queued"
    assert d.status == "ready"
    assert lesson.status == "failed"


# ── 7. Mock LLM gating ──────────────────────────────────────────────────────────

def test_mock_llm_dropped_outside_testing():
    assert Settings(app_env="development", llm_providers="groq,mock").effective_llm_providers == ["groq"]
    assert Settings(app_env="production", llm_providers="mock").effective_llm_providers == []
    assert Settings(app_env="testing", llm_providers="groq,mock").effective_llm_providers == ["groq", "mock"]
    assert Settings(app_env="development", llm_providers="mock", llm_allow_mock=True).effective_llm_providers == ["mock"]


async def test_lesson_gen_rejects_mock_output_when_not_allowed(monkeypatch):
    from app.course_builder.lesson_gen import LessonGenerationError, generate_lesson_page
    from app.course_builder.schema import OutlineLesson
    from app.llm.providers.mock import MockLLMProvider
    from app.llm.router import LLMRouter
    import app.course_builder.lesson_gen as lg

    async def no_kb(*a, **k):
        return ""
    monkeypatch.setattr(lg, "_retrieve_kb_passages", no_kb)
    monkeypatch.setattr(get_settings(), "app_env", "development")
    monkeypatch.setattr(get_settings(), "llm_allow_mock", False)
    lesson = OutlineLesson.model_validate({
        "id": "c1l1", "number": 1, "title": "Variables", "summary": "Variables hold values.",
        "objectives": ["Use variables"], "explanation_plan": ["What is a variable"],
        "practical": {"kind": "activity", "title": "Try it", "instructions": "Answer"},
        "tasks": [{"prompt": "q1"}, {"prompt": "q2"}],
    })
    with pytest.raises(LessonGenerationError, match="mock"):
        await generate_lesson_page(course_id="x", course_title="X", language="english", chapter_number=1,
                                   chapter_title="C", lesson=lesson,
                                   llm_router=LLMRouter([MockLLMProvider(latency_ms=0)], retry_per_provider=0))


# ── 8. Zip bombs ────────────────────────────────────────────────────────────────

def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_bomb_rejected_by_magic_and_parsers():
    from app.knowledge.parsing import ParseError
    from app.knowledge.parsing.docx import _parse_docx_sync
    from app.knowledge.parsing.magic import check_zip_safety, verify_magic
    bomb = _zip({"[Content_Types].xml": b"<x/>", "word/document.xml": b"\0" * (8 * 1024 * 1024)})
    assert check_zip_safety(bomb)
    ok, _, err = verify_magic(bomb, "evil.docx")
    assert not ok and "zip bomb" in err.lower()
    with pytest.raises(ParseError, match="zip bomb"):
        _parse_docx_sync(bomb)


def test_zip_limits_total_and_entries():
    from app.knowledge.parsing.magic import check_zip_safety
    many = _zip({f"word/f{i}.xml": b"<a/>" for i in range(30)})
    assert check_zip_safety(many, max_entries=10)
    assert check_zip_safety(many, max_total=50)
    assert check_zip_safety(_zip({"word/document.xml": os.urandom(4096)})) is None


# ── 9. YouTube subprocess hardening ─────────────────────────────────────────────

def test_youtube_subprocess_env_is_minimal(monkeypatch):
    from app.knowledge.sources import youtube
    monkeypatch.setenv("SECRET_KEY", "super-secret")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-secret")
    monkeypatch.setenv("YT_DLP_COOKIES", "/tmp/cookies.txt")
    env = youtube._subprocess_env()
    assert "SECRET_KEY" not in env and "GROQ_API_KEY" not in env
    assert env.get("YT_DLP_COOKIES") == "/tmp/cookies.txt"
    assert any(k.upper() == "PATH" for k in env)
    assert "@latest" not in youtube.ANALYZER_PKG_DEFAULT


async def test_run_cmd_timeout_kills_process():
    from app.knowledge.sources import youtube
    from app.knowledge.sources.base import SourceError
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    with pytest.raises(SourceError, match="timed out"):
        await youtube.run_cmd([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert loop.time() - t0 < 20


# ── 10. Reset token never logged; token_version revocation ──────────────────────

async def test_reset_token_not_logged(app_client, caplog):
    user, _ = await _new_user()
    sentinel = "SENTINEL-RESET-TOKEN-abc123"

    async def fake_create(db, user_id):
        return sentinel

    with patch("app.auth.router.create_password_reset_token", side_effect=fake_create):
        with caplog.at_level(logging.DEBUG):
            async with _client() as c:
                r = await c.post("/auth/password-reset/request", json={"email": user.email})
    assert r.status_code == 200
    assert sentinel not in caplog.text


async def test_logout_revokes_existing_tokens(app_client):
    _, token = await _new_user()
    async with _client(token) as c:
        assert (await c.get("/me/progress")).status_code == 200
        assert (await c.post("/auth/logout")).status_code == 200
        assert (await c.get("/me/progress")).status_code == 401


async def test_password_reset_revokes_tokens_and_is_single_use(app_client):
    from app.auth.service import create_password_reset_token
    user, token = await _new_user()
    async with AsyncSessionLocal() as db:
        raw = await create_password_reset_token(db, user.id)
    async with _client() as c:
        r = await c.post("/auth/password-reset/confirm", json={"token": raw, "new_password": "brand-new-pass-1"})
        assert r.status_code == 200
        r2 = await c.post("/auth/password-reset/confirm", json={"token": raw, "new_password": "another-pass-22"})
        assert r2.status_code == 400
    async with _client(token) as c:
        assert (await c.get("/me/progress")).status_code == 401


def test_expiry_columns_use_naive_utc():
    now = utcnow()
    assert now.tzinfo is None


async def test_ws_ticket_expiry_comparison(app_client, admin_user):
    from app.auth.ticket import consume_ticket, issue_ticket
    from app.db.models import WsTicket
    async with AsyncSessionLocal() as db:
        raw = await issue_ticket(db, admin_user.id, "")
        assert (await consume_ticket(db, raw, "")) is not None
        raw2 = await issue_ticket(db, admin_user.id, "")
        await db.execute(update(WsTicket).values(expires_at=utcnow() - timedelta(seconds=5)))
        await db.commit()
        assert (await consume_ticket(db, raw2, "")) is None


# ── 12. Search privacy (filtered in SQL, before LIMIT) ──────────────────────────

async def test_search_hides_private_books(app_client):
    from app.api.search import refresh_search_index
    from app.db.models import Chapter, Page
    owner, owner_tok = await _new_user()
    _, other_tok = await _new_user()
    cid = f"srch-{uuid.uuid4().hex[:6]}"
    word = f"zqx{uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as db:
        job = CourseBuildJob(course_id=cid, title="Secret", created_by=owner.id, status="ready")
        db.add(job)
        await db.flush()
        db.add(Course(id=cid, title="Secret", subtitle="", description=""))
        ch = Chapter(id=f"{cid}-c1", course_id=cid, number=1, title="C1")
        db.add(ch)
        await db.flush()
        db.add(Page(id=f"{cid}-c1-p1", course_id=cid, chapter_id=ch.id, page_number=1,
                    title=f"About {word}", narration_text=f"{word} is private"))
        db.add(LearnerBook(course_id=cid, job_id=job.id, owner_id=owner.id, visibility="private"))
        await db.commit()
        await refresh_search_index(db)
        await db.commit()
    async with _client(owner_tok) as c:
        mine = (await c.get("/search", params={"q": word})).json()
    async with _client(other_tok) as c:
        theirs = (await c.get("/search", params={"q": word})).json()
    async with _client() as c:
        anon = (await c.get("/search", params={"q": word})).json()
    assert [r["course_id"] for r in mine] == [cid]
    assert theirs == [] and anon == []


async def test_search_like_fallback(app_client):
    from app.api.search import _like_search
    async with AsyncSessionLocal() as db:
        rows = await _like_search(db, {"q": "python", "course_id": None, "uid": "", "is_admin": 0}, "")
    assert isinstance(rows, list) and rows, "LIKE fallback must work on every dialect"


# ── 18. Upload body size limits ─────────────────────────────────────────────────

async def test_oversized_upload_rejected_from_content_length(app_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_body_bytes", 1024)
    _, tok = await _new_user()
    async with _client(tok) as c:
        r = await c.post("/api/books", files={"file": ("big.pdf", b"%PDF" + b"0" * 5000, "application/pdf")})
    assert r.status_code == 413


# ── 14/15. WebSocket service: seek both ways, audio cap, task cleanup ────────────

async def test_seek_forward_and_backward_timed_mode():
    from app.services.mock_book_service import MockBookService
    sent: list[dict] = []

    async def send(d):
        sent.append(d)

    svc = MockBookService(send=send, token_ms=5, narration_tts=False)
    await svc.handle_start_page("python", "lesson_python_1_1")
    state = svc._narration
    if state is None:
        pytest.skip("seed page lesson_python_1_1 not available")
    await asyncio.sleep(0.05)
    target = min(len(state.words) - 1, 30)
    await svc.handle_seek(state.session_id, "", target)
    await asyncio.sleep(0.02)
    chunks = [d for d in sent if d.get("type") == "text_chunk"]
    assert chunks[-1]["payload"]["wordStart"] >= target
    sent.clear()
    await svc.handle_seek(state.session_id, "", 0)
    await asyncio.sleep(0.03)
    starts = [d["payload"]["wordStart"] for d in sent if d.get("type") == "text_chunk"]
    assert starts and starts[0] == 0, "seeking backward must replay from the earlier word"
    await svc.dispose()
    assert all(t.done() for t in svc._tasks) and svc._narration.task.done()


async def test_audio_in_buffer_is_capped(monkeypatch):
    import base64
    from app.services.mock_book_service import MockBookService
    monkeypatch.setattr(get_settings(), "ws_max_audio_bytes", 1000)
    sent: list[dict] = []

    async def send(d):
        sent.append(d)

    svc = MockBookService(send=send)
    chunk = base64.b64encode(b"\0" * 600).decode()
    await svc.handle_audio_in(session_id="s", seq=1, base64=chunk, final=False)
    await svc.handle_audio_in(session_id="s", seq=2, base64=chunk, final=False)
    assert svc._audio_buffers == {}
    assert any(d.get("type") == "error" and d["payload"]["code"] == "audio_too_large" for d in sent)
    await svc.dispose()


async def test_dispose_cancels_spawned_tasks():
    from app.services.mock_book_service import MockBookService

    async def send(d):
        pass

    svc = MockBookService(send=send)
    t = svc.spawn(asyncio.sleep(30))
    await svc.dispose()
    assert t.cancelled()
    assert svc.spawn(asyncio.sleep(0)) is None


# ── 20. LLM router overall deadline ─────────────────────────────────────────────

async def test_llm_router_deadline():
    from app.llm import LLMProvider
    from app.llm.router import LLMError, LLMRouter

    class Slow(LLMProvider):
        name = "slow"

        async def is_available(self):
            return True

        async def generate_text(self, prompt, system="", **kw):
            await asyncio.sleep(10)

        async def generate_json(self, prompt, schema, system="", **kw):
            await asyncio.sleep(10)

    router = LLMRouter([Slow(), Slow()], retry_per_provider=3, backoff_base_s=0.01, call_deadline_s=0.3)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    with pytest.raises(LLMError, match="deadline"):
        await router.generate_text("hi")
    assert loop.time() - t0 < 2


# ── 19. PDF page caps ───────────────────────────────────────────────────────────

def test_pdf_page_cap(monkeypatch):
    fitz = pytest.importorskip("fitz")
    from app.knowledge.parsing.pdf import _parse_pdf_sync
    doc = fitz.open()
    for i in range(6):
        doc.new_page().insert_text((72, 72), f"Page {i + 1} has enough words to count as real text content.")
    data = doc.tobytes()
    monkeypatch.setattr(get_settings(), "pdf_max_pages", 3)
    pages = _parse_pdf_sync(data, ocr_enabled=False)
    assert len(pages) == 3


# ── 21. Progress requires course access ─────────────────────────────────────────

async def test_progress_complete_denied_for_private_book(app_client):
    from app.db.models import Chapter, Page
    owner, owner_tok = await _new_user()
    _, other_tok = await _new_user()
    cid = f"prg-{uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as db:
        job = CourseBuildJob(course_id=cid, title="P", created_by=owner.id, status="ready")
        db.add(job)
        await db.flush()
        db.add(Course(id=cid, title="P", subtitle="", description=""))
        ch = Chapter(id=f"{cid}-c1", course_id=cid, number=1, title="C1")
        db.add(ch)
        await db.flush()
        db.add(Page(id=f"{cid}-c1-p1", course_id=cid, chapter_id=ch.id, page_number=1, title="T",
                    narration_text="n"))
        db.add(LearnerBook(course_id=cid, job_id=job.id, owner_id=owner.id, visibility="private"))
        await db.commit()
    url = f"/me/progress/{cid}/{cid}-c1-p1/complete"
    async with _client(other_tok) as c:
        assert (await c.post(url)).status_code == 404
    async with _client(owner_tok) as c:
        assert (await c.post(url)).status_code == 200


# ── 22. Production settings validation reads effective settings ─────────────────

def test_production_validation_rejects_weak_settings():
    weak = Settings(app_env="production", secret_key="rifas1234", debug=True,
                    database_url="postgresql+asyncpg://u:p@db/x", cors_origins=["https://a.example"],
                    llm_providers="groq,mock")
    errs = " | ".join(validate_production_settings(weak))
    assert "SECRET_KEY" in errs and "DEBUG" in errs and "Mock LLM" in errs

    good = Settings(app_env="production", secret_key="k" * 8 + uuid.uuid4().hex + uuid.uuid4().hex,
                    debug=False, database_url="postgresql+asyncpg://u:p@db/x",
                    cors_origins=["https://a.example"], llm_providers="groq,gemini")
    assert validate_production_settings(good) == []


def test_production_validation_requires_explicit_secret(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    s = Settings(app_env="production", debug=False, database_url="postgresql+asyncpg://u:p@db/x",
                 cors_origins=["https://a.example"], llm_providers="groq")
    assert any("explicitly set" in e for e in validate_production_settings(s))


# ── 17. Transliteration output never leaks into the global lexicon ──────────────

async def test_llm_transliteration_not_merged_globally(tmp_path, monkeypatch):
    import app.voice.cache as cache_mod
    import app.voice.transliterate as tr
    monkeypatch.setattr(cache_mod, "_cache_dir", lambda: tmp_path)

    async def fake_llm(sentence, words):
        return {w.lower(): "தவறு" for w in words}

    monkeypatch.setattr(tr, "_llm_transliterate", fake_llm)
    tr._LEARNED.clear()
    await tr.align_tanglish("Indha blorptastic nalla", use_llm=True)
    assert "blorptastic" not in tr._LEARNED
