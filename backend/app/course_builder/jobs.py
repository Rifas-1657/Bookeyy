"""
app/course_builder/jobs.py — durable-queue handlers for course builds (app/jobs).

    course_build.run       {jobId, hooks}                    full pipeline, resumable
    course_build.generate  {jobId, keys, regenerate, hooks}  (re)generate selected lessons

`hooks` is a name, resolved here at run time (callables can't be stored in the queue):
    "learner" → auto-publish the learner's private book after each lesson (books/service.py)
    None      → admin builder (publish is an explicit admin action)

When a queue job ends `failed` (attempts exhausted, incl. repeated worker crashes), the
build is marked failed so it stops counting against the learner's quota and can be retried.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select

from app.course_builder import runner
from app.db.database import AsyncSessionLocal
from app.db.models import CourseBuildJob, utcnow
from app.jobs.queue import ClaimedJob, active_keys
from app.jobs.worker import register

log = logging.getLogger(__name__)

ORPHAN_GRACE = timedelta(minutes=10)


def resolve_hooks(name: str | None, job_id: str) -> runner.JobHooks | None:
    if name == "learner":
        from app.books.service import learner_hooks
        return learner_hooks(job_id)
    return None


async def _dead(job: ClaimedJob, error: str) -> None:
    job_id = job.payload.get("jobId")
    if job_id:
        await runner.fail_build(job_id, f"Build stopped after {job.attempts} attempt(s): {error}")


@register(runner.RUN_KIND, on_dead=_dead)
async def run_build(job: ClaimedJob) -> dict:
    job_id = job.payload["jobId"]
    await runner.run_job(job_id, resolve_hooks(job.payload.get("hooks"), job_id),
                         final_attempt=job.last_attempt)
    return {"jobId": job_id}


@register(runner.GENERATE_KIND, on_dead=_dead)
async def generate_lessons(job: ClaimedJob) -> dict:
    job_id = job.payload["jobId"]
    keys = job.payload.get("keys") or None
    if job.payload.get("regenerate") and not job.is_retry:
        await runner.reset_lessons(job_id, keys)
    try:
        await runner._generate_many(job_id, keys, regenerate=False,
                                    hooks=resolve_hooks(job.payload.get("hooks"), job_id))
    except Exception as exc:
        if job.last_attempt:
            await runner.fail_build(job_id, f"Internal error: {exc}")
            return {"jobId": job_id, "error": str(exc)}
        raise
    return {"jobId": job_id}


async def fail_orphaned_builds() -> int:
    """Builds left non-terminal with no queue job at all (created by code before the durable
    queue, or a crash between create and enqueue) can never finish: fail them so they are
    retryable and stop counting against quotas. Recent ones get a grace period."""
    cutoff = utcnow() - ORPHAN_GRACE
    async with AsyncSessionLocal() as db:
        ids = list((await db.execute(select(CourseBuildJob.id).where(
            CourseBuildJob.status.in_(runner.NON_TERMINAL), CourseBuildJob.updated_at < cutoff))).scalars())
    if not ids:
        return 0
    busy = await active_keys(runner.queue_key(i) for i in ids)
    orphans = [i for i in ids if runner.queue_key(i) not in busy]
    for job_id in orphans:
        await runner.fail_build(job_id, runner.INTERRUPTED_ERROR)
    if orphans:
        log.warning("Marked %d orphaned course-build job(s) as failed (retryable)", len(orphans))
    return len(orphans)
