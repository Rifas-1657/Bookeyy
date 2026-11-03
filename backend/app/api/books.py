"""
app/api/books.py — learner "Create a book" API (P1).  Auth required, any role.

POST /api/books                     multipart (file?, url?, prompt?, title?, level?, language?, description?)
                                    or JSON {url?, prompt?, title?, level?, language?, description?}
                                    → 201 {jobId, courseId, status, title, origin, aiKnowledge, detectedType}
                                    + syllabus ("true": the file IS my syllabus), syllabusText (pasted syllabus)
POST /api/books/media               multipart: files (ONE video/audio, or 1..BOOKS_MAX_IMAGES images)
                                    + the same fields as above (url / prompt add material,
                                    syllabus=true marks the upload as the syllabus) → 201 (same shape,
                                    detectedType "video" | "audio" | "images").  Larger body limit
                                    (BOOKS_MAX_MEDIA_MB); transcription / OCR run in the job worker.
GET  /api/books/limits              → upload limits + what this server can read (vision / OCR / ffmpeg)
POST /api/books/detect              {url} → {type, url, label}   (no network; for the UI badge)
GET  /api/books                     → my books [{jobId, courseId, title, status, progress, …}]
GET  /api/books/quota               → {running, maxConcurrent, today, maxPerDay, exempt}
GET  /api/books/jobs/{id}           → job snapshot (same shape as the admin builder) + book fields
GET  /api/books/jobs/{id}/stream    → SSE; `event: job` data = that snapshot; `event: end` when done
POST /api/books/jobs/{id}/retry     failed book → 202 {jobId, status:"queued"} (resumes; 409 if not failed)

Jobs are owned by the creating user; other users get 404.  On lesson 1 the
book is auto-published as a private course (owner + admins only) and
republished as each further lesson lands.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from starlette.datastructures import UploadFile

from app.auth.dependencies import get_current_user
from app.books import service
from app.books.limits import AUDIO_EXTS, DOCUMENT_EXTS, IMAGE_EXTS, VIDEO_EXTS, get_media_limits
from app.books.service import BookError, MediaIn, UploadIn
from app.course_builder import runner
from app.course_builder.sse import job_event_stream
from app.db.models import User
from app.middleware.rate_limit import books_limit, limiter

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/books", tags=["books"])

_PRIVATE_REQUEST_KEYS = {"syllabusText"}


def _decorate(snap: dict, book) -> dict:
    snap = dict(snap)
    req = dict(snap.get("request") or {})
    for k in _PRIVATE_REQUEST_KEYS:
        req.pop(k, None)
    snap["request"] = req
    snap.update(service.book_fields(book))
    snap["bookReady"] = snap.get("publishedAt") is not None
    return snap


def _err(exc: BookError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


async def _create_from_fields(user: User, fields: dict, *, upload: UploadIn | None = None,
                              media: list[MediaIn] | None = None) -> dict:
    try:
        return await service.create_book(
            user,
            title=fields.get("title"),
            level=fields.get("level") or "beginner",
            language=fields.get("language") or "tanglish",
            description=fields.get("description") or "",
            url=fields.get("url"),
            prompt=fields.get("prompt"),
            upload=upload,
            media=media,
            syllabus=_truthy(fields.get("syllabus")),
            syllabus_text=fields.get("syllabusText") or fields.get("syllabus_text"),
        )
    except BookError as exc:
        raise _err(exc)


@router.post("", status_code=status.HTTP_201_CREATED)
@limiter.limit(books_limit)
async def create(request: Request, user: User = Depends(get_current_user)) -> dict:
    ctype = request.headers.get("content-type", "")
    upload: UploadIn | None = None
    if ctype.startswith("multipart/form-data") or ctype.startswith("application/x-www-form-urlencoded"):
        bs = service.get_book_settings()
        form = await request.form(max_files=1, max_fields=20)
        f = form.get("file")
        if isinstance(f, UploadFile) and (f.filename or f.size):
            data = await f.read(bs.max_upload_bytes + 1)
            upload = UploadIn(filename=f.filename or "upload", data=data)
        fields = {k: v for k, v in form.items() if isinstance(v, str)}
    else:
        try:
            fields = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="Send JSON or multipart/form-data.")
        if not isinstance(fields, dict):
            raise HTTPException(status_code=422, detail="Body must be an object.")
    return await _create_from_fields(user, fields, upload=upload)


@router.post("/media", status_code=status.HTTP_201_CREATED)
@limiter.limit(books_limit)
async def create_media(request: Request, user: User = Depends(get_current_user)) -> dict:
    """Video / audio / images → book. Files are spooled to disk, validated (magic bytes, size,
    duration via ffmpeg) and stored; the transcription / OCR happens in the durable job."""
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise HTTPException(status_code=422, detail="Send multipart/form-data with one or more `files`.")
    lim = get_media_limits()
    try:
        form = await request.form(max_files=lim.max_images + 1, max_fields=20)
    except HTTPException as exc:
        if exc.status_code == 413:
            raise
        raise HTTPException(status_code=422, detail=f"Too many files or fields (limit {lim.max_images} images).")
    uploads = [f for key in ("files", "file") for f in form.getlist(key)
               if isinstance(f, UploadFile) and (f.filename or f.size)]
    if not uploads:
        raise HTTPException(status_code=422, detail="Choose a video, an audio recording, or one or more images.")
    if len(uploads) > lim.max_images:
        raise HTTPException(status_code=422, detail=f"Too many images: {len(uploads)} (limit {lim.max_images}).")
    fields = {k: v for k, v in form.items() if isinstance(v, str)}
    cap = max(lim.max_media_bytes, lim.max_image_bytes)
    tmp = tempfile.mkdtemp(prefix="bookey_media_upl_")
    try:
        media: list[MediaIn] = []
        for i, f in enumerate(uploads):
            name = os.path.basename(f.filename or f"upload-{i}")[:200] or f"upload-{i}"
            ext = os.path.splitext(name)[1].lower()[:8]
            path = os.path.join(tmp, f"{i:03d}{ext}")
            h, size = hashlib.sha256(), 0
            with open(path, "wb") as out:
                while chunk := await f.read(1024 * 1024):
                    size += len(chunk)
                    if size > cap:
                        raise HTTPException(status_code=413,
                                            detail=f"{name} is larger than the {cap // (1024 * 1024)} MB limit.")
                    h.update(chunk)
                    out.write(chunk)
            media.append(MediaIn(filename=name, path=path, size=size, kind="", mime="", sha256=h.hexdigest()))
        try:
            media = await service.validate_media(media)
        except BookError as exc:
            raise _err(exc)
        return await _create_from_fields(user, fields, media=media)
    finally:
        for f in uploads:
            try:
                await f.close()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


@router.get("/limits")
async def limits(_: User = Depends(get_current_user)) -> dict:
    from app.knowledge.sources import images as img_mod
    from app.knowledge.sources import media as media_mod
    bs = service.get_book_settings()
    lim = get_media_limits()
    try:
        import faster_whisper  # noqa: F401
        whisper = True
    except Exception:
        whisper = False
    return {
        "maxUploadMb": bs.max_upload_bytes // (1024 * 1024),
        "maxMediaMb": lim.max_media_bytes // (1024 * 1024),
        "maxMediaMinutes": lim.max_media_seconds // 60,
        "maxImages": lim.max_images,
        "maxImageMb": lim.max_image_bytes // (1024 * 1024),
        "syllabusMaxChapters": lim.syllabus_max_chapters,
        "accepted": {"documents": list(DOCUMENT_EXTS), "video": list(VIDEO_EXTS),
                     "audio": list(AUDIO_EXTS), "images": [e for e in IMAGE_EXTS
                                                           if e not in (".heic", ".heif") or img_mod.heic_supported()]},
        "transcription": bool(media_mod.ffmpeg_path()) and whisper,
        "images": img_mod.capabilities(),
    }


@router.post("/detect")
async def detect(body: dict = Body(...), _: User = Depends(get_current_user)) -> dict:
    try:
        return service.detect_url(str(body.get("url") or ""))
    except BookError as exc:
        raise _err(exc)


@router.get("")
async def my_books(user: User = Depends(get_current_user)) -> list[dict]:
    return await service.list_books(user)


@router.get("/quota")
async def quota(user: User = Depends(get_current_user)) -> dict:
    return await service.quota_status(user)


async def _owned(job_id: str, user: User):
    book = await service.get_owned_book(job_id, user)
    if book is None:
        raise HTTPException(status_code=404, detail="Book job not found.")
    return book


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: User = Depends(get_current_user)) -> dict:
    book = await _owned(job_id, user)
    snap = await runner.snapshot(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Book job not found.")
    return _decorate(snap, book)


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry(job_id: str, user: User = Depends(get_current_user)) -> dict:
    await _owned(job_id, user)
    try:
        return await service.retry_book(job_id, user)
    except BookError as exc:
        raise _err(exc)


@router.get("/jobs/{job_id}/stream")
async def stream(job_id: str, user: User = Depends(get_current_user)) -> StreamingResponse:
    book = await _owned(job_id, user)
    return job_event_stream(job_id, decorate=lambda s: _decorate(s, book))
