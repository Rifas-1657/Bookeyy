"""
app/api/admin/course_builder.py — admin "build a course from many sources" API.

Mounted at /api/admin/course-builder (admin role required). JSON is camelCase;
request bodies accept camelCase or snake_case.

POST  /preview-source          {type?, url} → quick metadata (no ingest)
POST  /jobs                    CreateJobRequest → {jobId, courseId, status}
GET   /jobs                    → [job summaries]
GET   /jobs/{id}               → full job snapshot (?includePages=true adds lesson page JSON)
GET   /jobs/{id}/stream        → SSE; each event `data:` is the same snapshot shape
PATCH /jobs/{id}/outline       full CourseOutline (camelCase) → snapshot
POST  /jobs/{id}/generate      {lessonIds?, regenerate?} → {jobId, queued:[...]}
POST  /jobs/{id}/publish       {available?, includeUngenerated?} → publish summary
POST  /jobs/{id}/retry         failed job → 202 {jobId, status:"queued"} (resumes; 409 if not failed)

Work runs in the durable job queue (app/jobs): jobs survive restarts and resume.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.auth.dependencies import require_admin
from app.course_builder import runner
from app.course_builder.publish import publish_job
from app.course_builder.sse import job_event_stream
from app.course_builder.schema import CreateJobRequest, GenerateRequest, PreviewRequest, PublishRequest
from app.db.models import User
from app.knowledge.sources import SourceError, preview_source, validate_source

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/course-builder", tags=["admin-course-builder"])


def _validation_detail(exc: ValidationError) -> list[dict]:
    return [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()[:30]]


@router.post("/preview-source")
async def preview(body: PreviewRequest, _: User = Depends(require_admin)) -> dict:
    try:
        return (await preview_source(body.type, body.url)).to_api()
    except SourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        log.warning("preview failed for %s: %s", body.url, exc)
        raise HTTPException(status_code=502, detail=f"Could not reach source: {exc}")


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
async def create(body: CreateJobRequest, user: User = Depends(require_admin)) -> dict:
    # cheap synchronous validation (URL shape, SSRF, playlist rejection) before any DB write
    errors = []
    for i, s in enumerate(body.sources):
        try:
            if s.type not in ("upload", "syllabus"):
                s.url = validate_source(s.type, s.url)
        except SourceError as exc:
            errors.append({"index": i, "type": s.type, "url": s.url, "reason": str(exc)})
    if errors:
        raise HTTPException(status_code=422, detail={"message": "Invalid sources", "sources": errors})
    try:
        job = await runner.create_job(body, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await runner.start_job(job.id)
    return {"jobId": job.id, "courseId": job.course_id, "status": job.status}


@router.get("/jobs")
async def list_jobs(limit: int = 50, offset: int = 0, _: User = Depends(require_admin)) -> list[dict]:
    return await runner.list_jobs(limit=min(max(limit, 1), 200), offset=max(offset, 0))


async def _snap_or_404(job_id: str, include_pages: bool = False) -> dict:
    snap = await runner.snapshot(job_id, include_pages=include_pages)
    if snap is None:
        raise HTTPException(status_code=404, detail="Course build job not found.")
    return snap


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, include_pages: bool = Query(False, alias="includePages"),
                  _: User = Depends(require_admin)) -> dict:
    return await _snap_or_404(job_id, include_pages)


@router.get("/jobs/{job_id}/stream")
async def stream(job_id: str, _: User = Depends(require_admin)) -> StreamingResponse:
    await _snap_or_404(job_id)
    return job_event_stream(job_id)


@router.patch("/jobs/{job_id}/outline")
async def patch_outline(job_id: str, body: dict = Body(...), _: User = Depends(require_admin)) -> dict:
    snap = await _snap_or_404(job_id)
    if snap["busy"]:
        raise HTTPException(status_code=409, detail="Job is busy; wait until generation finishes.")
    data = body.get("outline", body) if isinstance(body, dict) else body
    try:
        await runner.update_outline(job_id, data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc))
    return await _snap_or_404(job_id)


@router.post("/jobs/{job_id}/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate(job_id: str, body: GenerateRequest | None = None, _: User = Depends(require_admin)) -> dict:
    body = body or GenerateRequest()
    snap = await _snap_or_404(job_id)
    if not snap.get("outline"):
        raise HTTPException(status_code=409, detail="Outline not ready yet.")
    if snap["busy"]:
        raise HTTPException(status_code=409, detail="Generation already running for this job.")
    keys = body.lesson_ids
    known = {l["id"] for l in snap["lessons"]}
    if keys:
        unknown = [k for k in keys if k not in known]
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown lesson ids: {unknown}")
    queued = [l["id"] for l in snap["lessons"]
              if (not keys or l["id"] in keys) and (body.regenerate or l["status"] != "done")]
    await runner.start_generation(job_id, keys, body.regenerate, hooks=await _hooks_for(job_id))
    return {"jobId": job_id, "queued": queued}


async def _hooks_for(job_id: str) -> str | None:
    """Learner books keep auto-publishing when an admin (re)generates or retries them."""
    from sqlalchemy import select
    from app.db.database import AsyncSessionLocal
    from app.db.models import LearnerBook
    async with AsyncSessionLocal() as db:
        is_book = (await db.execute(select(LearnerBook.id).where(LearnerBook.job_id == job_id).limit(1))).first()
    return "learner" if is_book else None


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry(job_id: str, _: User = Depends(require_admin)) -> dict:
    await _snap_or_404(job_id)
    try:
        await runner.retry_job(job_id, hooks=await _hooks_for(job_id))
    except runner.RetryNotAllowed as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"jobId": job_id, "status": "queued"}


@router.post("/jobs/{job_id}/publish")
async def publish(job_id: str, body: PublishRequest | None = None, _: User = Depends(require_admin)) -> dict:
    body = body or PublishRequest()
    await _snap_or_404(job_id)
    try:
        info = await publish_job(job_id, available=body.available, include_ungenerated=body.include_ungenerated)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await runner.mark_published(job_id, info)
    return info
