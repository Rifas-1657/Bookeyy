"""
app/course_builder/runner.py — course-build job orchestration.

    create_job(req, user_id)     → CourseBuildJob (persisted, queued)
    start_job(job_id, hooks)     → enqueue a durable "course_build.run" job:
                                    ingest → index → outline → lesson 1 → (optional) remaining lessons
    start_generation(job_id,…)   → enqueue "course_build.generate" (selected lessons)
    retry_job(job_id)            → failed build → reset failed parts, enqueue a resume
    snapshot(job_id)             → API dict {id,status,progress,steps,sources,outline,lessons,…}
    update_outline(job_id, data) → admin edit; resyncs lesson rows (changed lessons → pending)
    wait(job_id)                 → run/await the job's queued work (tests, CLI)

State lives in the DB (course_build_jobs / course_build_lessons) so the SSE
stream and GET endpoints work from any process.  The work itself runs in the
durable job queue (app/jobs; handlers in app/course_builder/jobs.py): at most
one active queue job per build (dedupe key "course_build:<id>").  Every step is
idempotent, so a build whose worker died is resumed after its lease expires:
indexed sources are skipped, a stored outline is reused, finished lessons are
kept, and the book is re-published.

Uploaded media (R2): "media" sources (a stored video/audio Document) are transcribed by
app/knowledge/sources/media.py and "images" sources (stored image Documents) are read by
app/knowledge/sources/images.py — both inside this job, with live step details
("Extracting audio", "Transcribing 12:30 / 45:00", "Reading image 2/5").  A source with
role="syllabus" contributes its text to request.syllabusText; with syllabusStrict the
outline follows that syllabus unit by unit.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
import secrets
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy import delete, select

from app.course_builder.lesson_gen import LessonGenerationError, generate_lesson_page
from app.course_builder.outline import ChunkRef, SourceContext, count_syllabus_units, generate_outline
from app.course_builder.schema import CourseOutline, CreateJobRequest
from app.course_builder.settings import get_cb_settings
from app.db.database import AsyncSessionLocal
from app.db.models import CourseBuildJob, CourseBuildLesson, DocumentChunk, LearnerBook, User
from app.knowledge.sources import SourceError, fetch_source
from app.knowledge.sources.ingest import IngestResult, clone_document_into_course, ingest_source_document
from app.knowledge.sources.web import syllabus_document

log = logging.getLogger(__name__)

PROMPT_STEP = ("syllabus", "Write a syllabus from your prompt")
STEP_DEFS = [
    ("ingest", "Fetch sources"),
    ("index", "Chunk & embed"),
    ("outline", "Draft course outline"),
    ("lesson1", "Generate first lesson"),
    ("lessons", "Generate remaining lessons"),
    ("publish", "Publish course"),
]
TERMINAL = {"ready", "failed"}
NON_TERMINAL = ("queued", "ingesting", "outlining", "generating")
INTERRUPTED_ERROR = "interrupted by server restart — retry"

RUN_KIND = "course_build.run"
GENERATE_KIND = "course_build.generate"

_LOCKS: dict[str, asyncio.Lock] = {}
_INDEX_LOCK: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None
# Test hook: override the LLM router used by outline + lesson generation.
LLM_ROUTER_OVERRIDE = None

HookFn = Callable[[str], Awaitable[None]]


@dataclass
class JobHooks:
    """Optional async callbacks (job_id) - used by the learner "Create a book" flow to
    auto-publish.  Hook errors are logged and turned into job warnings, never crash a job."""
    on_first_lesson: HookFn | None = None   # lesson 1 finished (before status flips to "ready")
    on_lesson: HookFn | None = None         # any later lesson finished
    on_complete: HookFn | None = None       # generation of the remaining lessons ended


def _lock(job_id: str) -> asyncio.Lock:
    if job_id not in _LOCKS:
        _LOCKS[job_id] = asyncio.Lock()
    return _LOCKS[job_id]


def _index_lock() -> asyncio.Lock:
    """One lock per event loop (a Lock that ever waited is bound to its loop; tests/CLI use several)."""
    global _INDEX_LOCK
    loop = asyncio.get_running_loop()
    if _INDEX_LOCK is None or _INDEX_LOCK[0] is not loop:
        _INDEX_LOCK = (loop, asyncio.Lock())
    return _INDEX_LOCK[1]


def queue_key(job_id: str) -> str:
    """Dedupe key of the durable queue job(s) working on this build."""
    return f"course_build:{job_id}"


async def busy_ids(job_ids: list[str]) -> set[str]:
    """Builds with queued/running queue work (works across processes)."""
    from app.jobs import queue
    keys = await queue.active_keys(queue_key(j) for j in job_ids)
    return {k.split(":", 1)[1] for k in keys}


async def is_busy(job_id: str) -> bool:
    return bool(await busy_ids([job_id]))


async def wait(job_id: str, timeout: float = 120.0) -> None:
    """Run (in this event loop) or wait for all queued work of a build until it is idle."""
    from app.jobs.worker import run_until_idle
    await run_until_idle(dedupe_keys=[queue_key(job_id)], timeout=timeout)


async def fail_build(job_id: str, message: str) -> None:
    """Terminal failure of a build (queue job dead / unrecoverable): free the learner's quota,
    mark running steps and lessons stuck in "generating" failed."""
    def _fail(j: CourseBuildJob) -> None:
        j.status, j.error = "failed", message[:2000]
        for st in j.steps:
            if st.get("status") == "running":
                st["status"], st["detail"] = "failed", message[:500]
    await _mutate(job_id, _fail)
    async with AsyncSessionLocal() as db:
        for row in (await db.execute(select(CourseBuildLesson).where(
                CourseBuildLesson.job_id == job_id, CourseBuildLesson.status == "generating"))).scalars():
            row.status, row.error = "failed", message[:2000]
        await db.commit()
    await _mutate(job_id, lambda j: None)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:max_len].rstrip("-") or "course")


def _initial_steps(prompt: bool = False) -> list[dict]:
    defs = ([PROMPT_STEP] if prompt else []) + STEP_DEFS
    return [{"key": k, "label": label, "status": "pending", "detail": ""} for k, label in defs]


def _set_step(steps: list[dict], key: str, status: str, detail: str | None = None) -> None:
    for s in steps:
        if s["key"] == key:
            s["status"] = status
            if detail is not None:
                s["detail"] = detail[:500]


def _lesson_hash(lesson: dict) -> str:
    return hashlib.sha256(json.dumps(lesson, sort_keys=True, default=str).encode()).hexdigest()


async def _mutate(job_id: str, fn: Callable[[CourseBuildJob], None]) -> None:
    """Load the job row, let `fn` edit deep-copied JSON fields, recompute progress, commit."""
    async with _lock(job_id):
        async with AsyncSessionLocal() as db:
            job = await db.get(CourseBuildJob, job_id)
            if job is None:
                return
            job.steps = copy.deepcopy(job.steps)
            job.sources = copy.deepcopy(job.sources)
            job.warnings = list(job.warnings or [])
            job.outline = copy.deepcopy(job.outline) if job.outline else job.outline
            fn(job)
            lessons = (await db.execute(
                select(CourseBuildLesson.status).where(CourseBuildLesson.job_id == job_id))).scalars().all()
            job.progress = compute_progress(job, list(lessons))
            job.updated_at = datetime.utcnow()
            await db.commit()


def compute_progress(job: CourseBuildJob, lesson_statuses: list[str]) -> int:
    steps = {s["key"]: s["status"] for s in job.steps or []}
    srcs = job.sources or []
    fin = sum(1 for s in srcs if s.get("status") in ("indexed", "failed"))
    p = 45.0 * (fin / len(srcs)) if srcs else (45.0 if steps.get("index") == "done" else 0.0)
    if steps.get("outline") == "done":
        p += 20
    if steps.get("lesson1") in ("done", "failed"):
        p += 10
    total = len(lesson_statuses)
    if total > 1:
        done_rest = max(0, sum(1 for s in lesson_statuses if s in ("done", "failed")) - 1)
        p += 25.0 * done_rest / (total - 1)
    elif steps.get("lesson1") in ("done", "failed"):
        p += 25
    return max(0, min(100, int(round(p))))


# ─────────────────────────────────────────────────────────────────────────────
# Create
# ─────────────────────────────────────────────────────────────────────────────

async def create_job(req: CreateJobRequest, user_id: str) -> CourseBuildJob:
    cb = get_cb_settings()
    if len(req.sources) > cb.max_sources:
        raise ValueError(f"Maximum {cb.max_sources} sources per course build.")
    course_id = req.course_id or f"{slugify(req.title)}-{secrets.token_hex(3)}"
    sources = []
    for i, s in enumerate(req.sources):
        sources.append({
            "index": i, "type": s.type, "url": s.url or "", "documentId": s.document_id,
            "title": s.title or ("Syllabus" if s.type == "syllabus" else s.url or s.document_id or ""),
            "status": "pending", "error": None, "documentIds": [], "chunkCount": 0, "metadata": {},
            **({"text": s.text} if s.type == "syllabus" else {}),
            **({"inputDocumentIds": list(s.document_ids or ([s.document_id] if s.document_id else []))}
               if s.type in ("media", "images") else {}),
            **({"role": s.role} if s.role else {}),
        })
    job = CourseBuildJob(
        course_id=course_id, title=req.title, status="queued", progress=0,
        request=req.dump(), steps=_initial_steps(bool(req.prompt and req.prompt.strip())), sources=sources, outline=None,
        warnings=[], created_by=user_id,
    )
    async with AsyncSessionLocal() as db:
        db.add(job)
        await db.commit()
        await db.refresh(job)
    return job


async def start_job(job_id: str, hooks: str | None = None) -> str:
    """Enqueue the build pipeline. `hooks` names a hook set resolved by the worker
    ("learner" = auto-publish the learner's private book; see app/course_builder/jobs.py)."""
    from app.jobs import queue
    qid, _ = await queue.enqueue(RUN_KIND, {"jobId": job_id, "hooks": hooks}, dedupe_key=queue_key(job_id))
    return qid


class RetryNotAllowed(Exception):
    pass


async def retry_job(job_id: str, hooks: str | None = None) -> str:
    """Retry a failed build: failed sources / steps / lessons go back to pending and a resume
    run is enqueued (indexed sources, the outline and finished lessons are kept)."""
    job = await _load(job_id)
    if job is None:
        raise LookupError(job_id)
    if job.status != "failed":
        raise RetryNotAllowed("Only failed jobs can be retried.")
    if await is_busy(job_id):
        raise RetryNotAllowed("This job is already queued or running.")

    def _reset(j: CourseBuildJob) -> None:
        j.status, j.error = "queued", None
        for st in j.steps:
            if st.get("status") in ("failed", "running"):
                st["status"], st["detail"] = "pending", ""
        for src in j.sources:
            if src.get("status") != "indexed":
                src.update(status="pending", error=None)
        j.warnings.append("Retried after a failure.")
    await _mutate(job_id, _reset)
    async with AsyncSessionLocal() as db:
        for row in (await db.execute(select(CourseBuildLesson).where(
                CourseBuildLesson.job_id == job_id,
                CourseBuildLesson.status.in_(("failed", "generating"))))).scalars():
            row.status, row.error = "pending", None
        await db.commit()
    await _mutate(job_id, lambda j: None)
    return await start_job(job_id, hooks)


async def add_warning(job_id: str, message: str) -> None:
    def _w(j: CourseBuildJob) -> None:
        if message not in j.warnings:
            j.warnings.append(message)
    await _mutate(job_id, _w)


async def _hook(job_id: str, fn: HookFn | None, name: str) -> None:
    if fn is None:
        return
    try:
        await fn(job_id)
    except Exception as exc:
        log.exception("hook %s failed for job %s", name, job_id)

        def _w(j: CourseBuildJob) -> None:
            j.warnings.append(f"{name} failed: {exc}"[:300])
        await _mutate(job_id, _w)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def _load(job_id: str) -> CourseBuildJob | None:
    async with AsyncSessionLocal() as db:
        return await db.get(CourseBuildJob, job_id)


async def run_job(job_id: str, hooks: JobHooks | None = None, *, final_attempt: bool = True) -> None:
    """Run (or resume) the whole pipeline. Unexpected errors are re-raised so the queue
    retries with backoff — unless this is the final attempt, then the build is failed."""
    try:
        await _run_job(job_id, hooks or JobHooks())
    except Exception as exc:
        log.exception("course build %s crashed", job_id)
        if not final_attempt:
            def _w(j: CourseBuildJob) -> None:
                j.warnings.append(f"Attempt failed, retrying: {exc}"[:300])
            await _mutate(job_id, _w)
            raise
        await fail_build(job_id, f"Internal error: {exc}")


_META_KEYS = ("thumbnail", "durationSeconds", "author", "method", "warnings", "repo", "branch",
              "stars", "language", "topics", "subreddit", "numComments", "pageCount",
              "sizeBytes", "authors", "published", "segmentCount", "selectedFiles",
              "imageCount", "frameCount", "sectionCount", "mediaKind", "diagrams")


def _public_meta(metadata: dict) -> dict:
    meta = {k: v for k, v in metadata.items() if k in _META_KEYS}
    if isinstance(meta.get("diagrams"), list):
        meta["diagrams"] = [str(d)[:300] for d in meta["diagrams"][:20]]
    return meta


async def _stored_inputs(src: dict) -> list[tuple[str, str, str]]:
    """[(filename, storage_key, mime)] of the raw uploads behind a media/images source."""
    from app.db.models import Document
    ids = src.get("inputDocumentIds") or ([src["documentId"]] if src.get("documentId") else [])
    out = []
    async with AsyncSessionLocal() as db:
        for did in ids:
            row = await db.get(Document, did)
            if row is None:
                raise SourceError("An uploaded file is missing — upload it again.")
            out.append((row.original_filename, row.storage_key, row.mime_type))
    if not out:
        raise SourceError("No uploaded files found for this source.")
    return out


async def _fetch_upload_media(src: dict, progress) -> list:
    """Stored video/audio or images → [SourceDocument] (runs the slow work: ffmpeg, whisper, vision)."""
    import tempfile
    from pathlib import Path
    from app.knowledge import get_storage
    storage = get_storage()
    inputs = await _stored_inputs(src)
    title = src.get("title") or inputs[0][0]
    if src["type"] == "images":
        from app.knowledge.sources.images import images_to_document
        items = [(name, await storage.retrieve(key)) for name, key, _m in inputs]
        return [await images_to_document(items, title=title, on_progress=progress)]

    from app.books.limits import get_media_limits
    from app.knowledge.sources.media import media_to_document
    name, key, mime = inputs[0]
    kind = "audio" if (mime or "").startswith("audio/") else "video"
    path = await storage.local_path(key)
    with tempfile.TemporaryDirectory(prefix="bookey_upl_") as tmp:
        if path is None:   # remote storage: copy down once
            path = str(Path(tmp) / ("input" + Path(name).suffix.lower()))
            Path(path).write_bytes(await storage.retrieve(key))
        return [await media_to_document(path, title=title, filename=name, kind=kind,
                                        max_seconds=get_media_limits().max_media_seconds,
                                        on_progress=progress)]


_SYL_NOISE = re.compile(r"^(Source:|Duration:|Spoken language:|## Page \d+|### Diagrams on page|_.*_$)")


def syllabus_text_from(text: str) -> str:
    """Syllabus text from an adapter's Markdown (drops the document header / page labels / timestamps)."""
    out = []
    lines = (text or "").splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]          # the adapter's document title
    for line in lines:
        if _SYL_NOISE.match(line.strip()):
            continue
        out.append(re.sub(r"^\[\d+(?::\d{2}){1,2}\]\s*", "", line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


async def _upload_text(document_id: str) -> str:
    """Plain text of an uploaded document (PDF/DOCX/PPTX/TXT/MD) — for syllabus files."""
    from app.db.models import Document
    from app.knowledge import get_storage
    from app.knowledge.parsing import parse_document
    async with AsyncSessionLocal() as db:
        row = await db.get(Document, document_id)
    if row is None:
        return ""
    pages = await parse_document(await get_storage().retrieve(row.storage_key), row.mime_type)
    return "\n".join(p.text for p in pages).strip()


async def _ingest_one(job_id: str, src: dict, course_id: str, user_id: str) -> None:
    idx = src["index"]

    def _upd(**kw):
        def fn(j: CourseBuildJob) -> None:
            j.sources[idx].update(kw)
        return fn

    async def _progress(detail: str) -> None:
        def fn(j: CourseBuildJob) -> None:
            j.sources[idx]["detail"] = detail[:200]
            many = len(j.sources) > 1
            _set_step(j.steps, "ingest", "running",
                      (f"{(j.sources[idx].get('title') or 'Source')[:60]}: " if many else "") + detail)
        await _mutate(job_id, fn)

    await _mutate(job_id, _upd(status="fetching"))
    syllabus_text = ""
    try:
        if src["type"] == "upload":
            await _mutate(job_id, _upd(status="indexing"))
            async with _index_lock():
                results = [await clone_document_into_course(src["documentId"], course_id=course_id, user_id=user_id)]
            meta = {}
            title = results[0].title
            if src.get("role") == "syllabus" and results[0].status == "indexed":
                syllabus_text = await _upload_text(src["documentId"])
        else:
            if src["type"] == "syllabus":
                docs = [syllabus_document(src.get("text") or "", title=src.get("title") or "Course syllabus")]
            elif src["type"] in ("media", "images"):
                docs = await _fetch_upload_media(src, _progress)
                await _progress("Indexing " + ("transcript" if src["type"] == "media" else "text"))
            else:
                docs = await fetch_source(src["type"], src["url"])
            if not docs:
                raise SourceError("Source produced no documents.")
            first = docs[0]
            meta = _public_meta(first.metadata)
            title = src.get("title") if src.get("title") and src["title"] != src["url"] else first.title
            if src.get("role") == "syllabus":
                syllabus_text = (src.get("text") or "") if src["type"] == "syllabus" else syllabus_text_from(first.text)
            await _mutate(job_id, _upd(status="indexing", title=title, metadata=meta,
                                       documentsFound=len(docs)))
            results: list[IngestResult] = []
            async with _index_lock():
                for d in docs:
                    results.append(await ingest_source_document(d, course_id=course_id, user_id=user_id))
        ok = [r for r in results if r.status == "indexed"]
        errs = [r.error for r in results if r.status != "indexed" and r.error]
        if not ok:
            raise SourceError(errs[0] if errs else "Indexing failed.")

        def _indexed(j: CourseBuildJob) -> None:
            j.sources[idx].update(
                status="indexed", title=title, error=("; ".join(errs)[:500] if errs else None),
                documentIds=[r.document_id for r in ok], chunkCount=sum(r.chunk_count for r in ok),
                detail="",
            )
            if syllabus_text.strip() and syllabus_text.strip()[:500] not in (j.request.get("syllabusText") or ""):
                # the learner's syllabus drives the outline (private: stripped from API snapshots)
                r = dict(j.request)
                r["syllabusText"] = ((r.get("syllabusText") or "") + "\n" + syllabus_text.strip()[:20_000]).strip()
                j.request = r
        await _mutate(job_id, _indexed)
    except (SourceError, ValueError) as exc:
        await _mutate(job_id, _upd(status="failed", error=str(exc)[:1000], detail=""))
    except Exception as exc:
        log.exception("source %s of job %s failed", idx, job_id)
        await _mutate(job_id, _upd(status="failed", error=f"Unexpected error: {exc}"[:1000], detail=""))


async def _source_contexts(job: CourseBuildJob) -> list[SourceContext]:
    out: list[SourceContext] = []
    async with AsyncSessionLocal() as db:
        for s in job.sources:
            if s.get("status") != "indexed" or not s.get("documentIds"):
                continue
            rows = (await db.execute(
                select(DocumentChunk).where(DocumentChunk.document_id.in_(s["documentIds"]))
                .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
            )).scalars().all()
            order = {d: i for i, d in enumerate(s["documentIds"])}
            rows = sorted(rows, key=lambda r: (order.get(r.document_id, 0), r.chunk_index))
            out.append(SourceContext(
                index=s["index"], type=s["type"], title=s.get("title") or s.get("url") or "",
                url=s.get("url") or "",
                language=(s.get("metadata") or {}).get("language") if s["type"] == "github" else None,
                chunks=[ChunkRef(r.id, r.document_id, s["index"], list(r.heading_path or []), r.text) for r in rows],
            ))
    return out


async def _sync_lessons(job_id: str, outline: CourseOutline) -> None:
    """Make course_build_lessons match the outline; changed lessons go back to pending."""
    async with AsyncSessionLocal() as db:
        existing = {l.lesson_key: l for l in (await db.execute(
            select(CourseBuildLesson).where(CourseBuildLesson.job_id == job_id))).scalars()}
        keep: set[str] = set()
        for ch in outline.chapters:
            for lesson in ch.lessons:
                h = _lesson_hash({"chapter": ch.title, "lesson": lesson.dump()})
                keep.add(lesson.id)
                row = existing.get(lesson.id)
                if row is None:
                    db.add(CourseBuildLesson(job_id=job_id, lesson_key=lesson.id, chapter_number=ch.number,
                                             lesson_number=lesson.number, title=lesson.title[:200],
                                             content_hash=h, status="pending", attempts=[]))
                elif row.content_hash != h:
                    row.content_hash, row.title = h, lesson.title[:200]
                    row.status, row.page, row.verified, row.error, row.attempts = "pending", None, False, None, []
        stale = [k for k in existing if k not in keep]
        if stale:
            await db.execute(delete(CourseBuildLesson).where(
                CourseBuildLesson.job_id == job_id, CourseBuildLesson.lesson_key.in_(stale)))
        await db.commit()


async def _write_prompt_syllabus(job_id: str, req: CreateJobRequest) -> CreateJobRequest | None:
    """Prompt-only book: LLM syllabus -> syllabusText + an indexed "syllabus" source."""
    from app.course_builder.outline import parse_syllabus
    from app.course_builder.prompt_book import AI_KNOWLEDGE_NOTE, write_syllabus
    cb = get_cb_settings()

    def _begin(j: CourseBuildJob) -> None:
        j.status = "outlining"
        _set_step(j.steps, "syllabus", "running", (req.prompt or "")[:120])
    await _mutate(job_id, _begin)
    try:
        res = await write_syllabus(
            req.prompt or "", level=req.level, language=req.language,
            max_chapters=req.options.max_chapters or min(cb.max_chapters, 5),
            max_lessons=min(req.options.lessons_per_chapter or 3, cb.max_lessons_per_chapter),
            llm_router=LLM_ROUTER_OVERRIDE,
        )
    except Exception as exc:
        log.exception("prompt syllabus failed for %s", job_id)

        def _f(j: CourseBuildJob) -> None:
            j.status, j.error = "failed", f"Could not plan the book: {exc}"[:2000]
            _set_step(j.steps, "syllabus", "failed", str(exc)[:500])
        await _mutate(job_id, _f)
        return None
    req.syllabus_text = ((req.syllabus_text or "") + "\n" + res.text).strip()
    n_ch = len(parse_syllabus(res.text))

    def _done(j: CourseBuildJob) -> None:
        r = dict(j.request)
        r["syllabusText"] = req.syllabus_text
        r["aiKnowledge"] = True
        j.request = r
        j.sources.append({
            "index": len(j.sources), "type": "syllabus", "url": "", "documentId": None,
            "title": f"AI syllabus: {res.title}"[:200], "status": "pending", "error": None,
            "documentIds": [], "chunkCount": 0, "metadata": {"generator": res.generator}, "text": res.text,
        })
        if AI_KNOWLEDGE_NOTE not in j.warnings:
            j.warnings.append(AI_KNOWLEDGE_NOTE)
        j.warnings.extend(res.warnings)
        _set_step(j.steps, "syllabus", "done",
                  f"{n_ch} chapters planned ({res.generator}" + (f", {res.provider}" if res.provider else "") + ")")
    await _mutate(job_id, _done)
    return req


async def _run_job(job_id: str, hooks: JobHooks) -> None:
    cb = get_cb_settings()
    job = await _load(job_id)
    if job is None:
        return
    req = CreateJobRequest.model_validate(job.request)

    # 0. Prompt-only book -> write a syllabus first ------------------------------
    if req.prompt and req.prompt.strip() and not (job.request or {}).get("aiKnowledge"):
        req = await _write_prompt_syllabus(job_id, req)
        if req is None:
            return
        job = await _load(job_id)

    # 1+2. Ingest + index (resume: sources already indexed are skipped) -----------
    todo_sources = [s for s in job.sources if s.get("status") != "indexed"]
    if todo_sources:
        def _start(j: CourseBuildJob) -> None:
            j.status = "ingesting"
            fin = sum(1 for s in j.sources if s.get("status") == "indexed")
            _set_step(j.steps, "ingest", "running", f"{fin}/{len(j.sources)} sources")
            _set_step(j.steps, "index", "running")
        await _mutate(job_id, _start)

        sem = asyncio.Semaphore(cb.fetch_concurrency)

        async def _guarded(src: dict) -> None:
            async with sem:
                await _ingest_one(job_id, src, job.course_id, job.created_by)
                def _count(j: CourseBuildJob) -> None:
                    fin = sum(1 for s in j.sources if s["status"] in ("indexed", "failed"))
                    _set_step(j.steps, "ingest", "running", f"{fin}/{len(j.sources)} sources")
                await _mutate(job_id, _count)

        await asyncio.gather(*[_guarded(s) for s in todo_sources])

    job = await _load(job_id)
    req = CreateJobRequest.model_validate(job.request)   # sources may have added syllabusText
    ok = [s for s in job.sources if s["status"] == "indexed"]
    failed = [s for s in job.sources if s["status"] == "failed"]
    have_syllabus = bool(req.syllabus_text and req.syllabus_text.strip())
    if job.sources and not ok and not have_syllabus:
        def _f(j: CourseBuildJob) -> None:
            j.status, j.error = "failed", "All sources failed to ingest."
            _set_step(j.steps, "ingest", "failed", f"0/{len(j.sources)} sources ingested")
            _set_step(j.steps, "index", "failed")
        await _mutate(job_id, _f)
        return

    chunks = sum(s.get("chunkCount", 0) for s in ok)
    reuse_outline = bool(job.outline)

    def _ingested(j: CourseBuildJob) -> None:
        _set_step(j.steps, "ingest", "done",
                  f"{len(ok)}/{len(j.sources)} sources ingested" + (f", {len(failed)} failed" if failed else ""))
        _set_step(j.steps, "index", "done", f"{chunks} chunks embedded")
        if failed:
            msg = (f"{len(failed)} source(s) failed: " +
                   "; ".join(f"[{s['index']}] {s.get('error') or 'error'}"[:200] for s in failed[:5]))
            if msg not in j.warnings:
                j.warnings.append(msg)
        if not reuse_outline:
            j.status = "outlining"
            _set_step(j.steps, "outline", "running")
    await _mutate(job_id, _ingested)

    # 3. Outline (resume: a stored outline is reused, never regenerated) -----------
    res = None
    if reuse_outline:
        outline = CourseOutline.model_validate(job.outline)
    else:
        contexts = await _source_contexts(job)
        max_ch = req.options.max_chapters or cb.max_chapters
        max_les = min(req.options.lessons_per_chapter or cb.max_lessons_per_chapter, cb.max_lessons_per_chapter)
        strict = bool(req.syllabus_strict and have_syllabus)
        per_chapter = req.options.lessons_per_chapter
        if strict:
            # the learner's syllabus decides how many lessons each unit gets
            per_chapter = None
            max_les = min(req.options.lessons_per_chapter or cb.max_lessons_per_chapter, cb.max_lessons_per_chapter)
            n_units = count_syllabus_units(req.syllabus_text or "")
            if n_units > max_ch:
                note = f"Your syllabus has {n_units} units; this book covers the first {max_ch}."

                def _wn(j: CourseBuildJob, note=note) -> None:
                    if note not in j.warnings:
                        j.warnings.append(note)
                await _mutate(job_id, _wn)
        visuals = [d for s in job.sources if s.get("status") == "indexed"
                   for d in ((s.get("metadata") or {}).get("diagrams") or [])]
        try:
            res = await generate_outline(
                title=req.title, description=req.description, level=req.level, language=req.language,
                target_audience=req.target_audience, syllabus_text=req.syllabus_text, sources=contexts,
                max_chapters=max_ch, lessons_per_chapter=per_chapter, max_lessons=max_les,
                context_chars=cb.outline_context_chars, max_tokens=cb.outline_max_tokens,
                llm_router=LLM_ROUTER_OVERRIDE, syllabus_strict=strict, visual_notes=visuals,
            )
        except Exception as exc:
            log.exception("outline failed for %s", job_id)
            def _of(j: CourseBuildJob) -> None:
                j.status, j.error = "failed", f"Outline generation failed: {exc}"[:2000]
                _set_step(j.steps, "outline", "failed", str(exc)[:500])
            await _mutate(job_id, _of)
            return
        outline = res.outline
        if not outline.title or outline.generator == "heuristic":
            outline.title = req.title
    await _sync_lessons(job_id, outline)
    n_lessons = sum(len(c.lessons) for c in outline.chapters)
    first_key = outline.chapters[0].lessons[0].id
    first_done = await _lesson_status(job_id, first_key) == "done"

    def _outlined(j: CourseBuildJob) -> None:
        if res is not None:
            j.outline = outline.dump()
            j.warnings.extend(res.warnings)
            _set_step(j.steps, "outline", "done",
                      f"{len(outline.chapters)} chapters, {n_lessons} lessons ({outline.generator}"
                      + (f", {res.provider}" if res.provider else "") + ")")
        else:
            _set_step(j.steps, "outline", "done")
        j.status = "generating"
        if not first_done:
            _set_step(j.steps, "lesson1", "running", outline.chapters[0].lessons[0].title)
    await _mutate(job_id, _outlined)

    # 4. Lesson 1 now; the rest after it (resume: finished lessons are kept) --------
    if not first_done:
        await _generate_one(job_id, first_key)
    await _hook(job_id, hooks.on_first_lesson, "Publishing")
    auto = req.options.auto_generate_remaining
    if auto is None:
        auto = cb.auto_generate_remaining

    def _after_first(j: CourseBuildJob) -> None:
        j.status = "ready"
        if n_lessons <= 1:
            _set_step(j.steps, "lessons", "skipped", "single-lesson course")
        elif not auto:
            _set_step(j.steps, "lessons", "pending", f"{n_lessons - 1} lessons waiting — POST /generate")
    await _mutate(job_id, _after_first)

    if auto and n_lessons > 1:
        await _generate_many(job_id, None, regenerate=False, hooks=hooks)
    else:
        await _hook(job_id, hooks.on_complete, "Publishing")


async def _lesson_status(job_id: str, lesson_key: str) -> str | None:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(CourseBuildLesson.status).where(
            CourseBuildLesson.job_id == job_id, CourseBuildLesson.lesson_key == lesson_key))).scalar_one_or_none()


async def _generate_one(job_id: str, lesson_key: str) -> bool:
    job = await _load(job_id)
    if job is None or not job.outline:
        return False
    outline = CourseOutline.model_validate(job.outline)
    found = next(((ch, l) for ch in outline.chapters for l in ch.lessons if l.id == lesson_key), None)
    if found is None:
        return False
    ch, lesson = found
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(CourseBuildLesson).where(
            CourseBuildLesson.job_id == job_id, CourseBuildLesson.lesson_key == lesson_key))).scalar_one_or_none()
        if row is None:
            return False
        row.status, row.error = "generating", None
        # Learner-created books must never execute LLM-written code (see page_gen/sandbox.py).
        is_learner_book = (await db.execute(
            select(LearnerBook.id).where(LearnerBook.job_id == job_id).limit(1))).first() is not None
        creator_role = (await db.execute(
            select(User.role).where(User.id == job.created_by))).scalar_one_or_none()
        is_learner_book = is_learner_book or creator_role != "admin"
        await db.commit()

    ok = True
    try:
        page, attempts = await generate_lesson_page(
            course_id=job.course_id, course_title=outline.title, language=outline.language,
            chapter_number=ch.number, chapter_title=ch.title, lesson=lesson, project=ch.project,
            llm_router=LLM_ROUTER_OVERRIDE, allow_execute=not is_learner_book,
        )
        values = dict(status="done", page=page.model_dump(by_alias=True, mode="json"), verified=page.verified,
                      attempts=attempts, error=None)
    except LessonGenerationError as exc:
        ok = False
        values = dict(status="failed", error=str(exc)[:2000], attempts=exc.attempts, page=None, verified=False)
    except Exception as exc:
        log.exception("lesson %s of job %s crashed", lesson_key, job_id)
        ok = False
        values = dict(status="failed", error=f"Unexpected error: {exc}"[:2000], attempts=[], page=None, verified=False)

    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(CourseBuildLesson).where(
            CourseBuildLesson.job_id == job_id, CourseBuildLesson.lesson_key == lesson_key))).scalar_one_or_none()
        if row is not None:
            for k, v in values.items():
                setattr(row, k, v)
            await db.commit()

    first_key = outline.chapters[0].lessons[0].id

    def _upd(j: CourseBuildJob) -> None:
        if lesson_key == first_key:
            _set_step(j.steps, "lesson1", "done" if ok else "failed",
                      lesson.title if ok else (values.get("error") or "")[:300])
    await _mutate(job_id, _upd)
    return ok


async def _generate_many(job_id: str, keys: list[str] | None, regenerate: bool,
                         hooks: JobHooks | None = None) -> None:
    hooks = hooks or JobHooks()
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(CourseBuildLesson).where(CourseBuildLesson.job_id == job_id)
                                 .order_by(CourseBuildLesson.chapter_number, CourseBuildLesson.lesson_number)
                                 )).scalars().all()
        todo = [r.lesson_key for r in rows
                if (keys is None or r.lesson_key in keys) and (regenerate or r.status not in ("done",))]
        total = len(rows)

    if not todo:
        def _noop(j: CourseBuildJob) -> None:
            if j.status in ("generating", "queued"):
                j.status = "ready"
            for st in j.steps:
                if st["key"] == "lessons" and st["status"] == "running":
                    st["status"] = "done"
        await _mutate(job_id, _noop)
        await _hook(job_id, hooks.on_complete, "Publishing")   # resume: re-publish
        return

    def _begin(j: CourseBuildJob) -> None:
        j.status = "generating"
        _set_step(j.steps, "lessons", "running", f"0/{len(todo)} lessons")
    await _mutate(job_id, _begin)

    done = failed = 0
    for key in todo:
        if await _generate_one(job_id, key):
            done += 1
            await _hook(job_id, hooks.on_lesson, "Publishing")
        else:
            failed += 1

        def _tick(j: CourseBuildJob, d=done, f=failed) -> None:
            _set_step(j.steps, "lessons", "running", f"{d + f}/{len(todo)} lessons" + (f", {f} failed" if f else ""))
        await _mutate(job_id, _tick)

    def _end(j: CourseBuildJob) -> None:
        j.status = "ready"
        _set_step(j.steps, "lessons", "done" if not failed else ("failed" if not done else "done"),
                  f"{done} generated" + (f", {failed} failed" if failed else "") + f" (of {total} lessons)")
    await _mutate(job_id, _end)
    await _hook(job_id, hooks.on_complete, "Publishing")


async def start_generation(job_id: str, keys: list[str] | None, regenerate: bool,
                           hooks: str | None = None) -> str:
    """Enqueue generation of the selected (or all not-done) lessons."""
    from app.jobs import queue
    qid, _ = await queue.enqueue(GENERATE_KIND, {"jobId": job_id, "keys": keys, "regenerate": regenerate,
                                                 "hooks": hooks}, dedupe_key=queue_key(job_id))
    return qid


async def reset_lessons(job_id: str, keys: list[str] | None) -> None:
    """regenerate=True, done before the first attempt: selected lessons back to pending, so a
    resumed attempt only regenerates what is still missing (idempotent)."""
    async with AsyncSessionLocal() as db:
        q = select(CourseBuildLesson).where(CourseBuildLesson.job_id == job_id)
        if keys:
            q = q.where(CourseBuildLesson.lesson_key.in_(keys))
        for row in (await db.execute(q)).scalars():
            row.status, row.error = "pending", None
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Outline edits + snapshots
# ─────────────────────────────────────────────────────────────────────────────

async def update_outline(job_id: str, data: dict) -> CourseOutline:
    outline = CourseOutline.model_validate(data)
    outline.generator = "admin"

    def _set(j: CourseBuildJob) -> None:
        j.outline = outline.dump()
    await _mutate(job_id, _set)
    await _sync_lessons(job_id, outline)
    await _mutate(job_id, lambda j: None)   # recompute progress after lesson resync
    return outline


async def mark_published(job_id: str, info: dict) -> None:
    def _p(j: CourseBuildJob) -> None:
        _set_step(j.steps, "publish", "done",
                  f"{info['chapters']} chapters / {info['pages']} pages → {info['courseId']}")
    await _mutate(job_id, _p)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _public_source(s: dict) -> dict:
    return {k: v for k, v in s.items() if k != "text"}


async def snapshot(job_id: str, *, include_outline: bool = True, include_pages: bool = False) -> dict | None:
    async with AsyncSessionLocal() as db:
        job = await db.get(CourseBuildJob, job_id)
        if job is None:
            return None
        lessons = (await db.execute(select(CourseBuildLesson).where(CourseBuildLesson.job_id == job_id)
                                    .order_by(CourseBuildLesson.chapter_number, CourseBuildLesson.lesson_number)
                                    )).scalars().all()
    out: dict[str, Any] = {
        "id": job.id,
        "courseId": job.course_id,
        "title": job.title,
        "status": job.status,
        "busy": await is_busy(job.id),
        "progress": job.progress,
        "steps": job.steps,
        "sources": [_public_source(s) for s in job.sources],
        "lessons": [{
            "id": l.lesson_key, "chapterNumber": l.chapter_number, "lessonNumber": l.lesson_number,
            "title": l.title, "status": l.status, "verified": l.verified, "error": l.error,
            **({"page": l.page} if include_pages else {}),
        } for l in lessons],
        "warnings": job.warnings or [],
        "error": job.error,
        "request": job.request,
        "createdAt": _iso(job.created_at),
        "updatedAt": _iso(job.updated_at),
        "publishedAt": _iso(job.published_at),
    }
    if include_outline:
        out["outline"] = job.outline
    return out


async def list_jobs(limit: int = 50, offset: int = 0) -> list[dict]:
    async with AsyncSessionLocal() as db:
        jobs = (await db.execute(select(CourseBuildJob).order_by(CourseBuildJob.created_at.desc())
                                 .limit(limit).offset(offset))).scalars().all()
    return [{
        "id": j.id, "courseId": j.course_id, "title": j.title, "status": j.status, "progress": j.progress,
        "sourceCount": len(j.sources or []),
        "chapterCount": len((j.outline or {}).get("chapters") or []),
        "lessonCount": sum(len(c.get("lessons") or []) for c in (j.outline or {}).get("chapters") or []),
        "error": j.error, "createdAt": _iso(j.created_at), "updatedAt": _iso(j.updated_at),
        "publishedAt": _iso(j.published_at),
    } for j in jobs]
