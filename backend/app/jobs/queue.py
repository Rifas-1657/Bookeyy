"""
app/jobs/queue.py — durable, DB-backed job queue (table `jobs`, app.db.models.Job).

Works on PostgreSQL (production) and SQLite (dev/tests):

    enqueue(kind, payload, dedupe_key=...)   → (job_id, created)   one active job per dedupe_key
    claim(worker_id, ...)                    → ClaimedJob | None   lease = now + lease_seconds
    heartbeat(job_id, worker_id)             → bool                renew the lease (False = lease lost)
    complete(job_id, worker_id, result)      → bool
    fail(job_id, worker_id, error, retry)    → "queued" | "failed" | None
    release(job_id, worker_id)               → bool                graceful shutdown: back to queued
    reap_expired()                           → [ClaimedJob]         expired leases with no attempts left → failed

Claiming
  PostgreSQL: SELECT … FOR UPDATE SKIP LOCKED picks the next row, then UPDATE in the
  same transaction — concurrent workers never block on or double-claim a row.
  SQLite: pick candidates, then an atomic conditional
      UPDATE jobs SET status='running', … WHERE id=:id AND (<claimable>)
  and keep the row only if rowcount == 1.

  <claimable> = (status='queued' AND run_after <= now)
             OR (status='running' AND lease_expires_at < now AND attempts < max_attempts)

  So a job whose worker died is picked up again once its lease expires (crash
  recovery / resume).  A job whose lease expired with no attempts left is failed by
  reap_expired() (poison-job cap).

Retries: fail(retry=True) puts the job back to `queued` with run_after = now + backoff,
backoff = min(base * 2**(attempts-1), max)  (JOBS_BACKOFF_BASE_SECONDS / _MAX_SECONDS).

All timestamps are naive UTC (app.db.models.utcnow), like the rest of the schema.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.database import AsyncSessionLocal
from app.db.models import JOB_ACTIVE_STATUSES, Job, utcnow

log = logging.getLogger(__name__)

TERMINAL_STATUSES = ("succeeded", "failed")


@dataclass
class ClaimedJob:
    id: str
    kind: str
    payload: dict
    attempts: int
    max_attempts: int
    dedupe_key: str | None = None
    worker_id: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def is_retry(self) -> bool:
        return self.attempts > 1

    @property
    def last_attempt(self) -> bool:
        return self.attempts >= self.max_attempts


def _claimed(row: Job, worker_id: str | None = None) -> ClaimedJob:
    return ClaimedJob(id=row.id, kind=row.kind, payload=dict(row.payload or {}), attempts=row.attempts,
                      max_attempts=row.max_attempts, dedupe_key=row.dedupe_key, worker_id=worker_id,
                      meta=dict(row.job_meta or {}))


def backoff_seconds(attempts: int) -> float:
    s = get_settings()
    return float(min(s.jobs_backoff_max_seconds, s.jobs_backoff_base_seconds * (2 ** max(0, attempts - 1))))


def _claimable(now: datetime):
    return or_(
        and_(Job.status == "queued", Job.run_after <= now),
        and_(Job.status == "running", Job.lease_expires_at.is_not(None), Job.lease_expires_at < now,
             Job.attempts < Job.max_attempts),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Enqueue
# ─────────────────────────────────────────────────────────────────────────────

async def _active_by_key(db: AsyncSession, dedupe_key: str) -> Job | None:
    return (await db.execute(select(Job).where(
        Job.dedupe_key == dedupe_key, Job.status.in_(JOB_ACTIVE_STATUSES)).limit(1))).scalar_one_or_none()


async def enqueue(kind: str, payload: dict | None = None, *, dedupe_key: str | None = None,
                  max_attempts: int | None = None, delay_seconds: float = 0.0, priority: int = 0,
                  course_id: str | None = None) -> tuple[str, bool]:
    """Insert a queued job. With `dedupe_key`, an existing queued/running job with the same
    key is returned instead (created=False); the partial unique index makes this race-safe."""
    s = get_settings()
    now = utcnow()
    async with AsyncSessionLocal() as db:
        if dedupe_key:
            existing = await _active_by_key(db, dedupe_key)
            if existing is not None:
                return existing.id, False
        job = Job(kind=kind, status="queued", payload=payload or {}, dedupe_key=dedupe_key,
                  max_attempts=max(1, max_attempts or s.jobs_max_attempts), priority=priority,
                  run_after=now + timedelta(seconds=max(0.0, delay_seconds)), course_id=course_id,
                  created_at=now, updated_at=now, attempts=0, job_meta={})
        db.add(job)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            if dedupe_key:
                existing = await _active_by_key(db, dedupe_key)
                if existing is not None:
                    return existing.id, False
            raise
        job_id = job.id
    _notify_local_workers()
    return job_id, True


# ─────────────────────────────────────────────────────────────────────────────
# Claim / lease
# ─────────────────────────────────────────────────────────────────────────────

async def claim(worker_id: str, *, kinds: Iterable[str] | None = None, dedupe_keys: Iterable[str] | None = None,
                lease_seconds: float | None = None) -> ClaimedJob | None:
    """Atomically claim the next runnable job (or None)."""
    lease = lease_seconds if lease_seconds is not None else get_settings().jobs_lease_seconds
    kinds = list(kinds) if kinds is not None else None
    keys = list(dedupe_keys) if dedupe_keys is not None else None
    now = utcnow()

    def _filters(stmt):
        stmt = stmt.where(_claimable(now))
        if kinds is not None:
            stmt = stmt.where(Job.kind.in_(kinds))
        if keys is not None:
            stmt = stmt.where(Job.dedupe_key.in_(keys))
        return stmt.order_by(Job.priority.desc(), Job.run_after, Job.created_at)

    values = dict(status="running", attempts=Job.attempts + 1, locked_by=worker_id,
                  lease_expires_at=now + timedelta(seconds=lease), started_at=now, updated_at=now)

    async with AsyncSessionLocal() as db:
        if db.bind.dialect.name == "postgresql":
            row_id = (await db.execute(
                _filters(select(Job.id)).limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
            if row_id is None:
                await db.rollback()
                return None
            await db.execute(update(Job).where(Job.id == row_id).values(**values)
                             .execution_options(synchronize_session=False))
            await db.commit()
        else:
            candidates = (await db.execute(_filters(select(Job.id)).limit(5))).scalars().all()
            row_id = None
            for cid in candidates:
                res = await db.execute(update(Job).where(Job.id == cid, _claimable(now)).values(**values)
                                       .execution_options(synchronize_session=False))
                if res.rowcount == 1:
                    row_id = cid
                    break
            await db.commit()
            if row_id is None:
                return None
        row = await db.get(Job, row_id, populate_existing=True)
        return _claimed(row, worker_id)


async def _owned_update(job_id: str, worker_id: str, **values: Any) -> bool:
    values.setdefault("updated_at", utcnow())
    async with AsyncSessionLocal() as db:
        res = await db.execute(update(Job).where(
            Job.id == job_id, Job.locked_by == worker_id, Job.status == "running").values(**values)
            .execution_options(synchronize_session=False))
        await db.commit()
        return res.rowcount == 1


async def heartbeat(job_id: str, worker_id: str, lease_seconds: float | None = None) -> bool:
    lease = lease_seconds if lease_seconds is not None else get_settings().jobs_lease_seconds
    return await _owned_update(job_id, worker_id, lease_expires_at=utcnow() + timedelta(seconds=lease))


async def complete(job_id: str, worker_id: str, result: dict | None = None) -> bool:
    now = utcnow()
    values: dict[str, Any] = dict(status="succeeded", finished_at=now, lease_expires_at=None, error=None)
    if result is not None:
        values["job_meta"] = result
    return await _owned_update(job_id, worker_id, **values)


async def fail(job_id: str, worker_id: str, error: str, *, retry: bool = True) -> str | None:
    """Record a failed attempt. Returns the new status ("queued" = will retry, "failed"),
    or None if this worker no longer owns the job."""
    async with AsyncSessionLocal() as db:
        row = await db.get(Job, job_id)
        if row is None or row.locked_by != worker_id or row.status != "running":
            return None
        attempts, max_attempts = row.attempts, row.max_attempts
    now = utcnow()
    if retry and attempts < max_attempts:
        ok = await _owned_update(job_id, worker_id, status="queued", error=error[:4000], locked_by=None,
                                 lease_expires_at=None,
                                 run_after=now + timedelta(seconds=backoff_seconds(attempts)))
        return "queued" if ok else None
    ok = await _owned_update(job_id, worker_id, status="failed", error=error[:4000], finished_at=now,
                             lease_expires_at=None)
    return "failed" if ok else None


async def release(job_id: str, worker_id: str) -> bool:
    """Graceful shutdown: hand the job back without spending an attempt."""
    return await _owned_update(job_id, worker_id, status="queued", locked_by=None, lease_expires_at=None,
                               run_after=utcnow(), attempts=Job.attempts - 1)


async def reap_expired() -> list[ClaimedJob]:
    """Fail running jobs whose lease expired and that have no attempts left.
    (Expired jobs WITH attempts left stay claimable — see claim().)"""
    now = utcnow()
    out: list[ClaimedJob] = []
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Job).where(
            Job.status == "running", Job.lease_expires_at.is_not(None), Job.lease_expires_at < now,
            Job.attempts >= Job.max_attempts))).scalars().all()
        for row in rows:
            msg = (f"worker lease expired on attempt {row.attempts}/{row.max_attempts} "
                   f"(worker {row.locked_by} died or hung)")
            res = await db.execute(update(Job).where(
                Job.id == row.id, Job.status == "running", Job.lease_expires_at < now).values(
                status="failed", finished_at=now, updated_at=now, lease_expires_at=None,
                error=((row.error + "\n") if row.error else "") + msg)
                .execution_options(synchronize_session=False))
            if res.rowcount == 1:
                c = _claimed(row)
                c.meta["error"] = msg
                out.append(c)
        await db.commit()
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Read helpers
# ─────────────────────────────────────────────────────────────────────────────

async def get(job_id: str) -> Job | None:
    async with AsyncSessionLocal() as db:
        return await db.get(Job, job_id)


async def active_keys(dedupe_keys: Iterable[str]) -> set[str]:
    """Which of these dedupe keys have a queued/running job."""
    keys = [k for k in dedupe_keys if k]
    if not keys:
        return set()
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Job.dedupe_key).where(
            Job.dedupe_key.in_(keys), Job.status.in_(JOB_ACTIVE_STATUSES)))).scalars().all()
    return set(rows)


async def latest_for_key(dedupe_key: str) -> Job | None:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(Job).where(Job.dedupe_key == dedupe_key)
                                 .order_by(Job.created_at.desc()).limit(1))).scalar_one_or_none()


async def next_run_after(dedupe_keys: Iterable[str]) -> datetime | None:
    keys = list(dedupe_keys)
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(Job.run_after).where(
            Job.dedupe_key.in_(keys), Job.status == "queued").order_by(Job.run_after).limit(1))).scalar_one_or_none()


# ─────────────────────────────────────────────────────────────────────────────
# In-process wake-up (enqueue → inline worker polls immediately)
# ─────────────────────────────────────────────────────────────────────────────

_WAKERS: list = []


def register_waker(fn) -> None:
    _WAKERS.append(fn)


def unregister_waker(fn) -> None:
    if fn in _WAKERS:
        _WAKERS.remove(fn)


def _notify_local_workers() -> None:
    for fn in list(_WAKERS):
        try:
            fn()
        except Exception:  # pragma: no cover - a dead loop must not break enqueue
            pass
