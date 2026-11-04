"""
app/main.py — FastAPI application entry point (B6).

Serving: pass the WebSocket frame cap to uvicorn so oversized frames are refused
by the server itself, e.g.
    uvicorn app.main:app --ws-max-size 524288        (= WS_MAX_MESSAGE_SIZE)
or run `python -m app.main`, which reads WS_MAX_MESSAGE_SIZE from settings.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.middleware.rate_limit import RateLimitMiddleware

from app.config import get_settings
from app.db.database import AsyncSessionLocal, engine
from app.db.seed import seed_database
from app.middleware.logging import RequestLoggingMiddleware
from app.middleware.rate_limit import limiter
from app.middleware.security import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.ws.gateway import book_ws_handler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Lifespan: create tables + seed on startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting Bookey backend B1 (%s)", settings.app_env)

    # Production config validation: fail fast on insecure settings.
    # Reads the EFFECTIVE settings (env vars + .env), not os.environ.
    if settings.app_env == "production":
        from app.config import validate_production_settings
        errors = validate_production_settings(settings)
        if errors:
            for error in errors:
                log.error("Production config error: %s", error)
            raise RuntimeError(
                f"Production configuration is invalid. {len(errors)} error(s) found:\n" +
                "\n".join(f"  - {e}" for e in errors)
            )
        if settings.code_runner_enabled:
            log.warning("CODE_RUNNER_ENABLED=true in production: LLM-written code will execute. "
                        "Only do this inside a real sandbox (container/nsjail/gVisor).")

    # Schema: SQLite dev/test → create_all (+ small dev fix-ups); PostgreSQL → owned by
    # Alembic (`alembic upgrade head`), startup only verifies the revision.
    from app.db.bootstrap import prepare_database
    await prepare_database(engine)

    # Seed the database with Python course data + admin user
    async with AsyncSessionLocal() as session:
        await seed_database(session)

    # FTS5 index for search (SQLite only; PostgreSQL uses the GIN tsvector index from
    # migration 0007). Built after seeding so a fresh database is searchable on first
    # boot; 'rebuild' resyncs the external content table instead of inserting duplicates.
    if engine.dialect.name == "sqlite":
        from sqlalchemy import text
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts
                USING fts5(title, narration_text, content='pages', content_rowid='rowid')
            """))
            await conn.execute(text("INSERT INTO pages_fts(pages_fts) VALUES('rebuild')"))

    # Durable job queue: course builds survive restarts (expired leases are requeued and
    # the build resumes). The worker runs here unless JOBS_INLINE_WORKER=false (then run
    # `python -m app.worker` separately).
    job_worker = None
    if settings.jobs_inline_worker:
        from app.jobs.worker import Worker
        job_worker = Worker(settings.jobs_concurrency)
        job_worker.start()

    log.info("Database ready")

    # Load the local female voices in the background so the first narrated
    # sentence doesn't pay the ~25 s model load. Skipped in tests/production-less setups.
    if settings.voice_enabled and settings.tts_provider.lower() == "local" and settings.app_env not in ("test", "testing"):
        import threading

        def _warm_voices() -> None:
            for mod in ("app.voice.tts.pocket_tts_provider", "app.voice.tts.kokoro_provider"):
                try:
                    __import__(mod, fromlist=["warm_up"]).warm_up()
                    log.info("Voice warm-up done: %s", mod.rsplit(".", 1)[-1])
                except Exception as exc:
                    log.warning("Voice warm-up skipped for %s: %s", mod, exc)

        threading.Thread(target=_warm_voices, name="voice-warmup", daemon=True).start()

    yield
    if job_worker is not None:
        log.info("Stopping job worker (running jobs are released back to the queue)")
        await job_worker.stop()
    log.info("Shutting down - draining open WebSocket connections")
    # Signal all open WS connections to close gracefully
    import asyncio
    for ws in list(_open_ws):
        try:
            await ws.close(1001, "Server shutting down")
        except Exception:
            pass
    if _open_ws:
        await asyncio.sleep(0.5)  # brief drain window
    await engine.dispose()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

# Graceful WS shutdown tracking
_open_ws: set[WebSocket] = set()

app = FastAPI(
    title="Bookey Backend B6",
    description="Adaptive interactive book reader — B0 through B6.",
    version="0.6.0-B6",
    lifespan=lifespan,
    openapi_url="/openapi.json" if settings.app_env != "production" else None,
)

# Rate limiter: default limit on every HTTP route (SlowAPIMiddleware) + stricter
# per-route limits via @limiter.limit (auth, POST /api/books, voice).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(RateLimitMiddleware)

# Security headers
app.add_middleware(SecurityHeadersMiddleware)

# Request logging with request IDs
app.add_middleware(RequestLoggingMiddleware)

# Upload size limits — refuse oversized bodies before anything reads them
# (inside CORS so the 413 still carries CORS headers).
app.add_middleware(BodySizeLimitMiddleware)

# CORS (must come after custom middleware in Starlette ordering)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

# Auth
from app.auth.router import router as auth_router
app.include_router(auth_router)

# B1 REST endpoints
from app.api.ws_ticket import router as ws_ticket_router
from app.api.courses import router as courses_router
from app.api.progress import router as progress_router
from app.api.bookmarks import router as bookmarks_router
from app.api.notes import router as notes_router
from app.api.profile import router as profile_router
from app.api.history import router as history_router
from app.api.notifications import router as notifications_router
from app.api.search import router as search_router
from app.api.admin.members import router as admin_members_router
from app.api.admin.courses import router as admin_courses_router
from app.api.admin.tutor import router as admin_tutor_router
from app.api.admin.documents import router as admin_documents_router
from app.api.admin.pages import router as admin_pages_router

app.include_router(ws_ticket_router)
app.include_router(courses_router)
app.include_router(progress_router)
app.include_router(bookmarks_router)
app.include_router(notes_router)
app.include_router(profile_router)
app.include_router(history_router)
app.include_router(notifications_router)
app.include_router(search_router)
app.include_router(admin_members_router)
app.include_router(admin_courses_router)
# The site's own pages own /admin/*, so its data calls come through the /api proxy.
app.include_router(admin_members_router, prefix="/api")
app.include_router(admin_courses_router, prefix="/api")
app.include_router(admin_tutor_router)
app.include_router(admin_documents_router)
app.include_router(admin_pages_router)
from app.api.admin.course_builder import router as admin_course_builder_router
app.include_router(admin_course_builder_router)
from app.api.books import router as books_router
app.include_router(books_router)

# B6: Personalization (bandit + FSRS)
from app.api.personalization import router as personalization_router
app.include_router(personalization_router)
# R3: Adaptive learning (learner model, graded quizzes, teaching-style bandit, spaced review)
from app.api.learning import router as learning_router
app.include_router(learning_router)
from app.api.surveys import router as surveys_router
app.include_router(surveys_router)

# Voice: TTS + STT endpoints
from app.api.voice import router as voice_router
app.include_router(voice_router)

# B6: Observability (/health, /ready, metrics)
from app.observability import register_observability
register_observability(app)

# B6: Provider metrics endpoint
from fastapi import APIRouter as _APIRouter
from app.auth.dependencies import require_admin as _require_admin

_obs_router = _APIRouter(prefix="/api/admin", tags=["observability"])

@_obs_router.get("/metrics/providers")
async def provider_metrics_endpoint(admin=__import__('fastapi').Depends(_require_admin)):
    from app.observability import provider_summary, get_provider_metrics
    return {"summary": provider_summary(), "recent": get_provider_metrics(50)}

app.include_router(_obs_router)

# Legacy B0 health + course/page routes (kept for backwards compat)
from app.api.routes import router as legacy_router
app.include_router(legacy_router)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/book")
async def ws_book(websocket: WebSocket) -> None:
    _open_ws.add(websocket)
    try:
        await book_ws_handler(websocket)
    finally:
        _open_ws.discard(websocket)


if __name__ == "__main__":  # pragma: no cover - manual serving helper
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port,
                ws_max_size=settings.ws_max_message_size)
