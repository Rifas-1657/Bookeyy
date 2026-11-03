"""
app/books/service.py — learner "Create a book" flow on top of the course builder.

    create_book(user, ...)   validate input + quota → store upload → CourseBuildJob
                             + LearnerBook row → runner.start_job(hooks="learner") (durable queue)
    retry_book(job_id, user) failed book → resume it (indexed sources / outline / lessons kept)
    quota_status(user)       {running, maxConcurrent, today, maxPerDay}
                             running = the user's builds that are queued, running, or still have
                             queued/running queue work (e.g. remaining lessons after lesson 1)
    list_books(user)         the user's books with job status

Env knobs (read on every call so tests can flip them):
  BOOKS_MAX_CONCURRENT        default 3    books building at once per user
  BOOKS_MAX_PER_DAY           default 10   books started per rolling 24 h per user
  BOOKS_MAX_UPLOAD_MB         default 20   upload size limit
  BOOKS_MAX_CHAPTERS          default 5    chapters per learner book
  BOOKS_LESSONS_PER_CHAPTER   default 3    lessons per chapter
  (media limits — BOOKS_MAX_MEDIA_MB / _MINUTES / BOOKS_MAX_IMAGES / BOOKS_MAX_IMAGE_MB /
   BOOKS_SYLLABUS_MAX_CHAPTERS / _LESSONS — live in app/books/limits.py)
Admins are exempt from the quota.

R2 inputs:
  validate_media(files)     video / audio / images → [MediaIn] (magic bytes, size, duration via ffmpeg)
  create_book(media=[...])  stores the raw files (Document status "raw") and builds a "media" or
                            "images" source — transcription / OCR run later inside the job worker
  syllabus=True / syllabus_text=...  the learner's syllabus drives the outline exactly
                            (request.syllabusStrict); other material fills the content
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import func, select

from app.books.access import is_admin
from app.books.limits import get_media_limits, media_kind
from app.course_builder import runner
from app.course_builder.prompt_book import is_unsafe_prompt, topic_from_prompt
from app.course_builder.publish import publish_job
from app.course_builder.schema import BuildOptions, CreateJobRequest, SourceSpec
from app.db.database import AsyncSessionLocal
from app.db.models import CourseBuildJob, Document, LearnerBook, User
import app.knowledge as knowledge
from app.knowledge.parsing.magic import verify_magic
from app.knowledge.sources import SourceError, detect_source_type, validate_source

log = logging.getLogger(__name__)

LEVELS = ("beginner", "intermediate", "advanced")
URL_TYPES = ("youtube", "github", "reddit", "arxiv", "pdf_url", "web")
SOURCE_LABELS = {
    "youtube": "YouTube video", "github": "GitHub repository", "reddit": "Reddit thread",
    "arxiv": "arXiv paper", "pdf_url": "PDF link", "web": "Web page",
}


class BookError(Exception):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class BookSettings:
    max_concurrent: int
    max_per_day: int
    max_upload_bytes: int
    max_chapters: int
    lessons_per_chapter: int


def get_book_settings() -> BookSettings:
    return BookSettings(
        max_concurrent=max(1, _int("BOOKS_MAX_CONCURRENT", 3)),
        max_per_day=max(1, _int("BOOKS_MAX_PER_DAY", 10)),
        max_upload_bytes=max(1, _int("BOOKS_MAX_UPLOAD_MB", 20)) * 1024 * 1024,
        max_chapters=min(20, max(1, _int("BOOKS_MAX_CHAPTERS", 5))),
        lessons_per_chapter=min(10, max(1, _int("BOOKS_LESSONS_PER_CHAPTER", 3))),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Quota
# ─────────────────────────────────────────────────────────────────────────────

async def quota_status(user: User) -> dict:
    bs = get_book_settings()
    since = datetime.utcnow() - timedelta(hours=24)
    async with AsyncSessionLocal() as db:
        today = (await db.execute(select(func.count(LearnerBook.id)).where(
            LearnerBook.owner_id == user.id, LearnerBook.created_at >= since))).scalar_one()
        rows = (await db.execute(
            select(CourseBuildJob.id, CourseBuildJob.status)
            .join(LearnerBook, LearnerBook.job_id == CourseBuildJob.id)
            .where(LearnerBook.owner_id == user.id, CourseBuildJob.status != "failed")
        )).all()
    busy = await runner.busy_ids([jid for jid, st in rows if st in runner.TERMINAL])
    running = sum(1 for jid, st in rows if st not in runner.TERMINAL or jid in busy)
    return {"running": running, "maxConcurrent": bs.max_concurrent,
            "today": int(today), "maxPerDay": bs.max_per_day, "exempt": is_admin(user)}


async def _check_quota(user: User) -> None:
    if is_admin(user):
        return
    q = await quota_status(user)
    if q["running"] >= q["maxConcurrent"]:
        raise BookError(f"You already have {q['running']} books being written. "
                        f"Wait for one to finish (limit {q['maxConcurrent']} at a time).", 429)
    if q["today"] >= q["maxPerDay"]:
        raise BookError(f"Daily limit reached: {q['maxPerDay']} books per 24 hours. Try again tomorrow.", 429)


# ─────────────────────────────────────────────────────────────────────────────
# Create
# ─────────────────────────────────────────────────────────────────────────────

def _title_from_filename(name: str) -> str:
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    stem = re.sub(r"[_\-.]+", " ", stem).strip()
    return stem[:1].upper() + stem[1:] if stem else ""


def _title_from_url(url: str, type_: str) -> str:
    p = urlparse(url)
    if type_ == "github":
        parts = [x for x in p.path.split("/") if x]
        return " / ".join(parts[:2]) if parts else "GitHub repository"
    if type_ in ("youtube", "reddit"):
        return SOURCE_LABELS[type_]
    last = [x for x in p.path.split("/") if x]
    slug = re.sub(r"[_\-.]+", " ", re.sub(r"\.(html?|pdf|php|aspx?)$", "", last[-1] if last else "")).strip()
    return (slug[:1].upper() + slug[1:]) if slug and not slug.isdigit() else (p.hostname or "Web page")


def _clean_title(t: str) -> str:
    t = " ".join((t or "").split())[:200]
    return t if len(t) >= 3 else (t + " book").strip()[:200] if t else "My book"


def detect_url(url: str) -> dict:
    """Cheap (no network) URL type detection + validation for the UI."""
    url = (url or "").strip()
    if not url:
        raise BookError("Paste a link first.")
    if "://" not in url and not re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", url):
        url = "https://" + url
    t = detect_source_type(url)
    try:
        norm = validate_source(t, url)
    except SourceError as exc:
        raise BookError(str(exc)) from exc
    return {"type": t, "url": norm, "label": SOURCE_LABELS.get(t, t)}


@dataclass
class UploadIn:
    filename: str
    data: bytes


@dataclass
class MediaIn:
    """A validated media upload, spooled to a local temp file by the API layer."""
    filename: str
    path: str
    size: int
    kind: str                 # video | audio | image
    mime: str
    sha256: str = ""
    duration: float | None = None


def _fmt_len(seconds: float) -> str:
    from app.knowledge.sources.base import fmt_ts
    return fmt_ts(seconds)


def _mb(n: int) -> str:
    return f"{n // (1024 * 1024)} MB"


async def validate_media(files: list[MediaIn]) -> list[MediaIn]:
    """Check kinds / counts / sizes / magic bytes / duration. Fills mime + duration. Raises BookError."""
    import asyncio
    from app.knowledge.sources import images as img_mod
    from app.knowledge.sources import media as media_mod
    from app.knowledge.sources.base import SourceError

    lim = get_media_limits()
    if not files:
        raise BookError("Choose a video, an audio recording, or one or more images.")
    for f in files:
        f.kind = media_kind(f.filename) or ""
        if f.kind == "document":
            raise BookError(f"{f.filename}: documents (PDF, PPTX, DOCX, TXT, MD) use the normal upload.", 415)
        if f.kind not in ("video", "audio", "image"):
            raise BookError(f"{f.filename}: unsupported file type. Use MP4, MOV, WEBM, MKV, M4A, MP3, WAV "
                            "or JPG, PNG, WEBP images.", 415)
        if f.size <= 0:
            raise BookError(f"{f.filename} is empty.")
    kinds = {f.kind for f in files}
    if "image" in kinds and len(kinds) > 1 or (kinds & {"video", "audio"} and len(files) > 1):
        raise BookError("Upload either ONE video/audio file or a set of images — not both at once.")
    if kinds == {"image"}:
        if len(files) > lim.max_images:
            raise BookError(f"Too many images: {len(files)} (limit {lim.max_images} per book).")
        if not (img_mod.vision_available() or img_mod.tesseract_cmd()):
            raise BookError("Reading images needs a vision model (e.g. OPENROUTER_API_KEY) or tesseract OCR; "
                            "neither is available on this server.", 503)
        for f in files:
            if f.size > lim.max_image_bytes:
                raise BookError(f"{f.filename} is larger than the {_mb(lim.max_image_bytes)} per-image limit.", 413)
            with open(f.path, "rb") as fh:
                head = fh.read(64)
            mime = img_mod.detect_image_mime(head)
            ext = os.path.splitext(f.filename)[1].lower()
            expected = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
                        ".heic": "image/heic", ".heif": "image/heic"}[ext]
            if mime != expected:
                raise BookError(f"{f.filename}: the file content is not a {ext[1:].upper()} image.", 415)
            if mime == "image/heic" and not img_mod.heic_supported():
                raise BookError(f"{f.filename}: HEIC photos aren't supported on this server yet — "
                                "export them as JPG (iPhone: Settings → Camera → Formats → Most Compatible).", 415)
            try:
                with open(f.path, "rb") as fh:
                    await asyncio.to_thread(img_mod.prepare_image, fh.read(), 64)
            except SourceError as exc:
                raise BookError(f"{f.filename}: {exc}", 415) from exc
            f.mime = mime
        return files

    f = files[0]
    if f.size > lim.max_media_bytes:
        raise BookError(f"{f.filename} is {_mb(f.size)} — the limit is {_mb(lim.max_media_bytes)}.", 413)
    with open(f.path, "rb") as fh:
        head = fh.read(64)
    mime = media_mod.detect_media_mime(head, f.filename)
    if mime is None:
        raise BookError(f"{f.filename}: the file content doesn't match its extension.", 415)
    if not media_mod.ffmpeg_path():
        raise BookError("Video and audio books need ffmpeg, which isn't installed on this server.", 503)
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        raise BookError("Video and audio books need local transcription (faster-whisper), "
                        "which isn't installed on this server.", 503)
    try:
        info = await media_mod.probe_media(f.path)
    except SourceError as exc:
        raise BookError(f"{f.filename}: {exc}", 415) from exc
    if not info.has_audio and not info.has_video:
        raise BookError(f"{f.filename}: no audio or video found in this file.", 415)
    if info.duration and info.duration > lim.max_media_seconds + 5:
        raise BookError(f"{f.filename} is {_fmt_len(info.duration)} long — the limit is "
                        f"{_fmt_len(lim.max_media_seconds)}.", 413)
    if f.kind == "video" and not info.has_video:
        f.kind = "audio"
    f.mime, f.duration = mime, info.duration
    return files


def _images_title(files: list[MediaIn]) -> str:
    stem = os.path.splitext(os.path.basename(files[0].filename))[0]
    if len(re.findall(r"\d", stem)) >= 6 or re.match(r"^(img|pxl|dsc|photo|image|scan|whatsapp)", stem, re.I):
        return f"Notes from {len(files)} photo{'s' if len(files) != 1 else ''}"
    return _title_from_filename(files[0].filename)


async def create_book(
    user: User,
    *,
    title: str | None = None,
    level: str = "beginner",
    language: str = "tanglish",
    description: str = "",
    url: str | None = None,
    prompt: str | None = None,
    upload: UploadIn | None = None,
    media: list[MediaIn] | None = None,
    syllabus: bool = False,
    syllabus_text: str | None = None,
) -> dict:
    bs = get_book_settings()
    lim = get_media_limits()
    media = media or []
    syllabus_text = (syllabus_text or "").strip() or None
    if syllabus_text and len(syllabus_text) > 20_000:
        raise BookError("The syllabus text is too long (max 20,000 characters).")
    if syllabus and upload is None and not media and not syllabus_text:
        raise BookError("Mark a file as your syllabus, or paste the syllabus text.")
    strict = bool(syllabus or syllabus_text)
    url = (url or "").strip() or None
    prompt = " ".join((prompt or "").split()) or None
    title = (title or "").strip() or None
    level = (level or "beginner").strip().lower()
    language = (language or "tanglish").strip().lower()
    if level not in LEVELS:
        raise BookError(f"level must be one of {', '.join(LEVELS)}.")
    if language not in ("tanglish", "english"):
        raise BookError("language must be 'tanglish' or 'english'.")
    if upload is None and not media and not url and not prompt and not syllabus_text:
        raise BookError("Give me something to work with: upload a file, paste a link, or type a prompt.")
    if prompt and len(prompt) < 4:
        raise BookError("The prompt is too short — describe what you want to learn.")
    if prompt and len(prompt) > 2000:
        raise BookError("The prompt is too long (max 2000 characters).")
    if prompt and is_unsafe_prompt(prompt):
        raise BookError("I can't write a book on that topic. Try a different subject.")

    # validate inputs before touching quota / storage
    detected = detect_url(url) if url else None
    mime = None
    if upload is not None:
        if not upload.data:
            raise BookError("The uploaded file is empty.")
        if len(upload.data) > bs.max_upload_bytes:
            raise BookError(f"File is larger than the {bs.max_upload_bytes // (1024 * 1024)} MB limit.", 413)
        ok, mime, err = verify_magic(upload.data, upload.filename or "upload")
        if not ok:
            raise BookError(err or "Unsupported file type. Use PDF, PPTX, DOCX, TXT or MD.", 415)

    await _check_quota(user)

    if title is None:
        if media:
            title = _images_title(media) if media[0].kind == "image" else _title_from_filename(media[0].filename)
        elif upload is not None:
            title = _title_from_filename(upload.filename)
        elif detected is not None:
            title = _title_from_url(detected["url"], detected["type"])
        elif prompt:
            title = topic_from_prompt(prompt)
    title = _clean_title(title or "My book")
    course_id = f"book-{runner.slugify(title, 30)}-{secrets.token_hex(3)}"

    sources: list[SourceSpec] = []
    origin_parts: list[str] = []
    source_label = ""
    if upload is not None:
        data = upload.data
        fname = os.path.basename(upload.filename or "upload")[:200] or "upload"
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "bin"
        storage_key = f"documents/{course_id}/{secrets.token_hex(16)}.{ext}"
        await knowledge.get_storage().store(storage_key, data)
        doc_id = str(uuid.uuid4())
        async with AsyncSessionLocal() as db:
            db.add(Document(
                id=doc_id, course_id=course_id, original_filename=fname, storage_key=storage_key,
                mime_type=mime, size_bytes=len(data), status="uploaded", uploaded_by=user.id,
                source_type="file", content_hash=hashlib.sha256(data).hexdigest(),
            ))
            await db.commit()
        sources.append(SourceSpec(type="upload", document_id=doc_id, title=fname,
                                  role="syllabus" if syllabus else None))
        origin_parts.append("upload")
        source_label = fname
    if media:
        media_ids: list[str] = []
        for m in media:
            fname = os.path.basename(m.filename or "upload")[:200] or "upload"
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "bin"
            storage_key = f"documents/{course_id}/raw-{secrets.token_hex(16)}.{ext}"
            await knowledge.get_storage().store_file(storage_key, m.path)
            doc_id = str(uuid.uuid4())
            async with AsyncSessionLocal() as db:
                # "raw": the original file; the job indexes a transcript / OCR document instead
                db.add(Document(
                    id=doc_id, course_id=course_id, original_filename=fname, storage_key=storage_key,
                    mime_type=m.mime or "application/octet-stream", size_bytes=m.size, status="raw",
                    uploaded_by=user.id, source_type="file", content_hash=m.sha256 or None,
                ))
                await db.commit()
            media_ids.append(doc_id)
        role = "syllabus" if syllabus and upload is None else None
        if media[0].kind == "image":
            names = ", ".join(os.path.basename(m.filename) for m in media[:3]) + ("…" if len(media) > 3 else "")
            label = f"{len(media)} image{'s' if len(media) != 1 else ''} ({names})"
            sources.append(SourceSpec(type="images", document_ids=media_ids, title=title, role=role))
            origin_parts.append("images")
        else:
            label = os.path.basename(media[0].filename)
            sources.append(SourceSpec(type="media", document_id=media_ids[0], title=title, role=role))
            origin_parts.append(media[0].kind)
        source_label = source_label or label
    if syllabus_text:
        sources.append(SourceSpec(type="syllabus", text=syllabus_text, title="Your syllabus", role="syllabus"))
        origin_parts.append("syllabus")
        source_label = source_label or "Your syllabus"
    if detected is not None:
        sources.append(SourceSpec(type=detected["type"], url=detected["url"]))
        origin_parts.append("url")
        source_label = source_label or detected["url"]

    prompt_only = bool(prompt) and not sources
    if prompt_only:
        origin_parts.append("prompt")
        source_label = prompt[:300]
    origin = origin_parts[0] if len(origin_parts) == 1 else "mixed"
    # only the syllabus itself and no other material → lessons are filled from AI knowledge
    syllabus_only = strict and all(s.role == "syllabus" for s in sources)
    max_chapters = lim.syllabus_max_chapters if strict else bs.max_chapters
    lessons = lim.syllabus_max_lessons if strict else bs.lessons_per_chapter

    req = CreateJobRequest(
        title=title,
        # with material, the prompt steers focus; without, it drives an AI-written syllabus
        description=(description or (prompt if prompt and not prompt_only else "") or "")[:2000],
        level=level,
        language=language,
        target_audience=f"{level} learners",
        sources=sources,
        syllabus_text=syllabus_text,
        syllabus_strict=strict,
        prompt=prompt if prompt_only else None,
        course_id=course_id,
        options=BuildOptions(max_chapters=max_chapters, lessons_per_chapter=lessons,
                             auto_generate_remaining=True),
    )
    ai_knowledge = prompt_only or syllabus_only
    job = await runner.create_job(req, user.id)
    if syllabus_only:
        await runner.add_warning(job.id, SYLLABUS_ONLY_NOTE)
    async with AsyncSessionLocal() as db:
        db.add(LearnerBook(course_id=course_id, job_id=job.id, owner_id=user.id, origin=origin,
                           source_label=source_label[:300], visibility="private", ai_knowledge=ai_knowledge))
        await db.commit()
    await runner.start_job(job.id, hooks="learner")
    if media:
        detected_type = "images" if media[0].kind == "image" else media[0].kind
    else:
        detected_type = detected["type"] if detected else ("upload" if upload else ("syllabus" if syllabus_text else "prompt"))
    return {"jobId": job.id, "courseId": course_id, "status": job.status, "title": title,
            "origin": origin, "aiKnowledge": ai_knowledge, "syllabus": strict,
            "detectedType": detected_type}


SYLLABUS_ONLY_NOTE = ("Follows your syllabus; the lesson content is written from AI knowledge because no other "
                      "material was added. Double-check important facts.")


# ─────────────────────────────────────────────────────────────────────────────
# Auto-publish hooks
# ─────────────────────────────────────────────────────────────────────────────

async def publish_learner_book(job_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        book = (await db.execute(select(LearnerBook).where(LearnerBook.job_id == job_id))).scalar_one_or_none()
        owner = await db.get(User, book.owner_id) if book else None
    if book is None:
        raise LookupError("no learner book for job")
    cover = {"accent": "gold", "source": "learner-book", "origin": book.origin,
             "aiGenerated": book.ai_knowledge, "visibility": book.visibility}
    overrides = {
        "category": "My books",
        "author": ((owner.display_name if owner and owner.display_name else "You") + "'s book")[:120],
        "available": True,
    }
    if book.ai_knowledge:
        overrides["subtitle"] = "Generated from AI knowledge"
    info = await publish_job(job_id, available=True, include_ungenerated=True, overrides=overrides, cover=cover)
    await runner.mark_published(job_id, info)
    return info


async def retry_book(job_id: str, user: User) -> dict:
    """Owner (or admin) retries a failed book. Counts against the concurrent-build quota."""
    async with AsyncSessionLocal() as db:
        job = await db.get(CourseBuildJob, job_id)
    if job is None:
        raise BookError("Book job not found.", 404)
    if job.status != "failed":
        raise BookError("Only failed books can be retried.", 409)
    if not is_admin(user):
        q = await quota_status(user)
        if q["running"] >= q["maxConcurrent"]:
            raise BookError(f"You already have {q['running']} books being written. "
                            f"Wait for one to finish (limit {q['maxConcurrent']} at a time).", 429)
    try:
        await runner.retry_job(job_id, hooks="learner")
    except runner.RetryNotAllowed as exc:
        raise BookError(str(exc), 409) from exc
    return {"jobId": job_id, "status": "queued"}


def learner_hooks(job_id: str) -> runner.JobHooks:
    return runner.JobHooks(on_first_lesson=publish_learner_book, on_lesson=publish_learner_book,
                           on_complete=publish_learner_book)


# ─────────────────────────────────────────────────────────────────────────────
# Read
# ─────────────────────────────────────────────────────────────────────────────

async def get_owned_book(job_id: str, user: User) -> LearnerBook | None:
    async with AsyncSessionLocal() as db:
        book = (await db.execute(select(LearnerBook).where(LearnerBook.job_id == job_id))).scalar_one_or_none()
    if book is None or not (book.owner_id == user.id or is_admin(user)):
        return None
    return book


def book_fields(book: LearnerBook) -> dict:
    return {"origin": book.origin, "sourceLabel": book.source_label, "visibility": book.visibility,
            "aiKnowledge": book.ai_knowledge, "courseUrl": f"/book/{book.course_id}"}


async def list_books(user: User, limit: int = 50) -> list[dict]:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(LearnerBook, CourseBuildJob)
            .join(CourseBuildJob, CourseBuildJob.id == LearnerBook.job_id)
            .where(LearnerBook.owner_id == user.id)
            .order_by(LearnerBook.created_at.desc()).limit(limit)
        )).all()
    busy = await runner.busy_ids([j.id for _, j in rows])
    return [{
        "jobId": j.id, "courseId": b.course_id, "title": (j.outline or {}).get("title") or j.title,
        "status": j.status, "progress": j.progress, "busy": j.id in busy, "error": j.error,
        "published": j.published_at is not None,
        "createdAt": b.created_at.isoformat() + "Z" if b.created_at else None,
        **book_fields(b),
    } for b, j in rows]
