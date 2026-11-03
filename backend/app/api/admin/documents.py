"""
app/api/admin/documents.py  —  Phase-1 source-ingestion admin API.

Endpoints
─────────
POST  /api/admin/documents/upload-batch
    Upload 1–20 files at once.  Creates one SourceJob and one Document per
    file.  Validates magic bytes, size, deduplication pre-check, and course
    existence before writing anything.  Background task drives the job.

POST  /api/admin/documents/ingest-urls
    Submit 1–20 URLs.  Creates one SourceJob and one Document per URL with
    status "uploaded" and storage_key="url:<url>".  The worker fetches,
    stores and indexes each URL.

GET   /api/admin/documents
    Paginated list of documents, optional ?course_id= filter.

GET   /api/admin/documents/jobs
    List SourceJob records (most recent first).

GET   /api/admin/documents/jobs/{job_id}
    Single SourceJob with its child document summaries.

GET   /api/admin/documents/{doc_id}
    Single document detail.

DELETE /api/admin/documents/{doc_id}
    Delete document, its chunks, its storage bytes, and its vectors.

POST  /api/admin/documents/{doc_id}/retry
    Re-queue a failed/duplicate document.

GET   /api/admin/documents/{doc_id}/chunks
    List extracted chunks (debug / inspection).

GET   /api/admin/documents/status-stream
    SSE stream of status updates for the current course (2-second polling).

Security
────────
- All endpoints require admin role.
- File magic-byte check (not just extension).
- Random storage key — original filename never used as storage path.
- Size limit enforced before touching storage.
- URL validation (SSRF, scheme, private-IP) done synchronously before any DB
  write; network fetch happens inside the background worker only.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime
from typing import AsyncGenerator

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.config import get_settings
from app.db.database import get_db
from app.db.models import Course, Document, DocumentChunk, SourceJob, User
from app.knowledge import get_embedding_backend, get_storage, get_vector_store
from app.knowledge.parsing.magic import verify_magic
from app.knowledge.url_fetcher import FetchError, validate_url
from app.knowledge.worker import process_document, process_job

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/documents", tags=["admin-documents"])
settings = get_settings()

_MAX_BATCH = 20          # maximum files / URLs per single submission
_URL_KEY_PREFIX = "url:" # sentinel storage_key for unresolved URL sources


# ─────────────────────────────────────────────────────────────────────────────
# Response schemas
# ─────────────────────────────────────────────────────────────────────────────

class DocumentOut(BaseModel):
    id: str
    course_id: str
    original_filename: str
    mime_type: str
    size_bytes: int
    status: str
    error_message: str | None
    page_count: int | None
    chunk_count: int | None
    embedding_model: str | None
    uploaded_by: str
    source_type: str
    source_url: str | None
    source_job_id: str | None
    content_hash: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SourceJobOut(BaseModel):
    id: str
    course_id: str
    kind: str
    status: str
    total_items: int
    done_items: int
    failed_items: int
    progress_pct: int
    error_summary: str | None
    submitted_by: str
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class SourceJobDetail(SourceJobOut):
    documents: list[DocumentOut]


class BatchUploadResult(BaseModel):
    job_id: str
    accepted: list[DocumentOut]
    rejected: list[dict]   # [{filename, reason}]


class UrlIngestRequest(BaseModel):
    course_id: str
    urls: list[str]

    @field_validator("urls")
    @classmethod
    def validate_count(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one URL is required.")
        if len(v) > _MAX_BATCH:
            raise ValueError(f"Maximum {_MAX_BATCH} URLs per request.")
        return v


class UrlIngestResult(BaseModel):
    job_id: str
    accepted: list[DocumentOut]
    rejected: list[dict]   # [{url, reason}]


class ChunkOut(BaseModel):
    id: str
    chunk_index: int
    page_number: int
    heading_path: list[str]
    token_count: int
    text_preview: str

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    message: str


# ─────────────────────────────────────────────────────────────────────────────
# POST /upload-batch  — multi-file upload
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/upload-batch",
    status_code=status.HTTP_201_CREATED,
    response_model=BatchUploadResult,
)
async def upload_batch(
    files: list[UploadFile] = File(...),
    course_id: str = Form(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> BatchUploadResult:
    """
    Upload 1–20 files for a course in one request.
    Returns immediately with per-file accepted/rejected breakdown.
    Indexing runs in the background via a SourceJob.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")
    if len(files) > _MAX_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {_MAX_BATCH} files per request.",
        )

    # Validate course
    course = await db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail=f"Course '{course_id}' not found.")

    storage = get_storage()
    accepted: list[Document] = []
    rejected: list[dict] = []

    for upload in files:
        fname = upload.filename or "unknown"
        data = await upload.read()

        # Size check
        if not data:
            rejected.append({"filename": fname, "reason": "File is empty."})
            continue
        if len(data) > settings.max_document_size_bytes:
            rejected.append({
                "filename": fname,
                "reason": (
                    f"File size {len(data) // (1024*1024)} MB exceeds the "
                    f"{settings.max_document_size_bytes // (1024*1024)} MB limit."
                ),
            })
            continue

        # Magic-byte check
        ok, mime, err = verify_magic(data, fname)
        if not ok:
            rejected.append({"filename": fname, "reason": err or "Unsupported file type."})
            continue

        # Content-hash duplicate check (within this course)
        content_hash = hashlib.sha256(data).hexdigest()
        dup_result = await db.execute(
            select(Document).where(
                Document.course_id == course_id,
                Document.content_hash == content_hash,
                Document.status.in_(["indexed", "processing", "uploaded"]),
            )
        )
        dup = dup_result.scalar_one_or_none()
        if dup is not None:
            rejected.append({
                "filename": fname,
                "reason": (
                    f"Identical file already exists as '{dup.original_filename}' "
                    f"(id {dup.id}, status {dup.status})."
                ),
            })
            continue

        # Persist to storage
        rand_prefix = secrets.token_hex(16)
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "bin"
        storage_key = f"documents/{course_id}/{rand_prefix}.{ext}"
        try:
            await storage.store(storage_key, data)
        except Exception as exc:
            rejected.append({"filename": fname, "reason": f"Storage error: {exc}"})
            continue

        doc = Document(
            id=str(uuid.uuid4()),
            course_id=course_id,
            original_filename=fname,
            storage_key=storage_key,
            mime_type=mime,
            size_bytes=len(data),
            status="uploaded",
            uploaded_by=current_user.id,
            source_type="file",
            content_hash=content_hash,
        )
        db.add(doc)
        accepted.append(doc)

    if not accepted:
        # Nothing accepted — return 200 with rejection info (no job created)
        await db.rollback()
        return BatchUploadResult(
            job_id="",
            accepted=[],
            rejected=rejected,
        )

    # Create SourceJob
    job = SourceJob(
        id=str(uuid.uuid4()),
        course_id=course_id,
        kind="file",
        status="queued",
        total_items=len(accepted),
        submitted_by=current_user.id,
    )
    db.add(job)
    await db.flush()   # get job.id

    for doc in accepted:
        doc.source_job_id = job.id

    await db.commit()
    for doc in accepted:
        await db.refresh(doc)
    await db.refresh(job)

    background_tasks.add_task(process_job, job.id)

    log.info(
        "upload-batch: job=%s course=%s accepted=%d rejected=%d",
        job.id, course_id, len(accepted), len(rejected),
    )

    return BatchUploadResult(
        job_id=job.id,
        accepted=[DocumentOut.model_validate(d) for d in accepted],
        rejected=rejected,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /ingest-urls  — URL batch ingestion
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/ingest-urls",
    status_code=status.HTTP_201_CREATED,
    response_model=UrlIngestResult,
)
async def ingest_urls(
    body: UrlIngestRequest,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UrlIngestResult:
    """
    Submit 1–20 URLs for a course.
    Each URL is syntactically validated and SSRF-checked before any DB write.
    The actual network fetch + indexing happens in the background via a SourceJob.
    """
    # Validate course
    course = await db.get(Course, body.course_id)
    if course is None:
        raise HTTPException(status_code=404, detail=f"Course '{body.course_id}' not found.")

    accepted: list[Document] = []
    rejected: list[dict] = []
    seen_in_batch: set[str] = set()

    for raw_url in body.urls:
        url = raw_url.strip()
        if not url:
            continue

        # Intra-batch dedup
        if url in seen_in_batch:
            rejected.append({"url": url, "reason": "Duplicate URL in this submission."})
            continue
        seen_in_batch.add(url)

        # Syntactic + SSRF validation (no network fetch yet)
        try:
            validate_url(url)
        except FetchError as exc:
            rejected.append({"url": url, "reason": str(exc)})
            continue

        # Check if this URL was already ingested for this course
        dup_result = await db.execute(
            select(Document).where(
                Document.course_id == body.course_id,
                Document.source_url == url,
                Document.status.in_(["indexed", "processing", "uploaded", "failed"]),
            )
        )
        dup = dup_result.scalar_one_or_none()
        if dup is not None:
            rejected.append({
                "url": url,
                "reason": (
                    f"URL already ingested as '{dup.original_filename}' "
                    f"(id {dup.id}, status {dup.status})."
                ),
            })
            continue

        # Derive a display filename from the URL path
        from urllib.parse import urlparse as _urlparse
        path_part = _urlparse(url).path.rstrip("/") or "document"
        fname = path_part.rsplit("/", 1)[-1] or "document"
        if not fname:
            fname = "document"

        doc = Document(
            id=str(uuid.uuid4()),
            course_id=body.course_id,
            original_filename=fname,
            # Sentinel storage key — replaced by the worker after fetch
            storage_key=f"{_URL_KEY_PREFIX}{url}",
            mime_type="application/octet-stream",  # resolved by worker after fetch
            size_bytes=0,
            status="uploaded",
            uploaded_by=current_user.id,
            source_type="url",
            source_url=url,
        )
        db.add(doc)
        accepted.append(doc)

    if not accepted:
        await db.rollback()
        return UrlIngestResult(job_id="", accepted=[], rejected=rejected)

    # Create SourceJob
    job = SourceJob(
        id=str(uuid.uuid4()),
        course_id=body.course_id,
        kind="url",
        status="queued",
        total_items=len(accepted),
        submitted_by=current_user.id,
    )
    db.add(job)
    await db.flush()

    for doc in accepted:
        doc.source_job_id = job.id

    await db.commit()
    for doc in accepted:
        await db.refresh(doc)
    await db.refresh(job)

    background_tasks.add_task(process_job, job.id)

    log.info(
        "ingest-urls: job=%s course=%s accepted=%d rejected=%d",
        job.id, body.course_id, len(accepted), len(rejected),
    )

    return UrlIngestResult(
        job_id=job.id,
        accepted=[DocumentOut.model_validate(d) for d in accepted],
        rejected=rejected,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /jobs  —  list SourceJobs
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/jobs", response_model=list[SourceJobOut])
async def list_jobs(
    course_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[SourceJobOut]:
    q = (
        select(SourceJob)
        .order_by(SourceJob.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if course_id:
        q = q.where(SourceJob.course_id == course_id)
    result = await db.execute(q)
    return [SourceJobOut.model_validate(j) for j in result.scalars()]


# ─────────────────────────────────────────────────────────────────────────────
# GET /jobs/{job_id}  —  single SourceJob with children
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}", response_model=SourceJobDetail)
async def get_job(
    job_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> SourceJobDetail:
    job = await db.get(SourceJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="SourceJob not found.")
    result = await db.execute(
        select(Document)
        .where(Document.source_job_id == job_id)
        .order_by(Document.created_at)
    )
    docs = result.scalars().all()
    # Build the detail manually to avoid lazy-loading the relationship outside async context
    out = SourceJobDetail(
        id=job.id,
        course_id=job.course_id,
        kind=job.kind,
        status=job.status,
        total_items=job.total_items,
        done_items=job.done_items,
        failed_items=job.failed_items,
        progress_pct=job.progress_pct,
        error_summary=job.error_summary,
        submitted_by=job.submitted_by,
        created_at=job.created_at,
        finished_at=job.finished_at,
        documents=[DocumentOut.model_validate(d) for d in docs],
    )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GET /  —  list documents
# ─────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[DocumentOut])
async def list_documents(
    course_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentOut]:
    q = (
        select(Document)
        .order_by(Document.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if course_id:
        q = q.where(Document.course_id == course_id)
    result = await db.execute(q)
    return [DocumentOut.model_validate(d) for d in result.scalars()]


# ─────────────────────────────────────────────────────────────────────────────
# GET /{doc_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{doc_id}", response_model=DocumentOut)
async def get_document(
    doc_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return DocumentOut.model_validate(doc)


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /{doc_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/{doc_id}", status_code=204, response_class=Response)
async def delete_document(
    doc_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    storage = get_storage()
    # Only delete from storage if key is real (not the url-sentinel)
    if not doc.storage_key.startswith(_URL_KEY_PREFIX):
        try:
            await storage.delete(doc.storage_key)
        except Exception:
            log.warning("Could not delete storage key %s", doc.storage_key)

    emb = get_embedding_backend()
    vs = get_vector_store()
    try:
        await vs.delete_by_document(doc.course_id, emb.name, doc_id)
    except Exception:
        log.warning("Could not delete vectors for document %s", doc_id)

    await db.delete(doc)
    await db.commit()
    log.info("document deleted: id=%s", doc_id)
    return Response(status_code=204)


# ─────────────────────────────────────────────────────────────────────────────
# POST /{doc_id}/retry
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{doc_id}/retry", response_model=DocumentOut)
async def retry_document(
    doc_id: str,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    if doc.status not in ("failed", "uploaded", "duplicate"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Document status is '{doc.status}'. "
                "Only failed, uploaded, or duplicate documents can be retried."
            ),
        )
    doc.status = "uploaded"
    doc.error_message = None
    # For duplicates that were re-submitted, clear the hash so dedup re-runs
    if doc.status == "duplicate":
        doc.content_hash = None
    await db.commit()
    await db.refresh(doc)
    background_tasks.add_task(process_document, doc_id)
    return DocumentOut.model_validate(doc)


# ─────────────────────────────────────────────────────────────────────────────
# GET /{doc_id}/chunks  —  debug inspection
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{doc_id}/chunks", response_model=list[ChunkOut])
async def list_chunks(
    doc_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ChunkOut]:
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == doc_id)
        .order_by(DocumentChunk.chunk_index)
    )
    return [
        ChunkOut(
            id=c.id,
            chunk_index=c.chunk_index,
            page_number=c.page_number,
            heading_path=c.heading_path,
            token_count=c.token_count,
            text_preview=c.text[:200],
        )
        for c in result.scalars()
    ]


# ─────────────────────────────────────────────────────────────────────────────
# GET /status-stream  —  SSE live updates
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/status-stream")
async def status_stream(
    course_id: str | None = None,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """
    Server-Sent Events: pushes document and job status changes every 2 s.
    Streams for up to 5 minutes (150 × 2 s), then closes.
    """
    async def _generator() -> AsyncGenerator[str, None]:
        seen_docs: dict[str, str] = {}
        seen_jobs: dict[str, str] = {}

        for _ in range(150):
            # Documents
            dq = select(Document).order_by(Document.created_at.desc()).limit(100)
            if course_id:
                dq = dq.where(Document.course_id == course_id)
            doc_rows = (await db.execute(dq)).scalars().all()
            for doc in doc_rows:
                key = f"{doc.id}:{doc.status}:{doc.chunk_count}"
                if seen_docs.get(doc.id) != key:
                    seen_docs[doc.id] = key
                    yield f"data: {json.dumps({'kind': 'document', 'id': doc.id, 'status': doc.status, 'error': doc.error_message, 'chunk_count': doc.chunk_count})}\n\n"

            # Jobs
            jq = select(SourceJob).order_by(SourceJob.created_at.desc()).limit(20)
            if course_id:
                jq = jq.where(SourceJob.course_id == course_id)
            job_rows = (await db.execute(jq)).scalars().all()
            for job in job_rows:
                key = f"{job.status}:{job.progress_pct}"
                if seen_jobs.get(job.id) != key:
                    seen_jobs[job.id] = key
                    yield f"data: {json.dumps({'kind': 'job', 'id': job.id, 'status': job.status, 'progress_pct': job.progress_pct, 'done_items': job.done_items, 'failed_items': job.failed_items, 'total_items': job.total_items})}\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
