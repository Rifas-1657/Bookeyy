"""
app/jobs/worker.py — job handlers registry + the worker loop.

    @register("course_build.run", on_dead=...)      # see app/course_builder/jobs.py
    async def handler(job: ClaimedJob) -> dict | None: ...

    Worker(concurrency=2).run_forever()     # `python -m app.worker`, or inline in the API
    await run_until_idle(dedupe_keys=[...])  # drive matching jobs in the current loop (tests / CLI)

A handler that raises is retried with exponential backoff until max_attempts, then the
job is failed and the handler's `on_dead(job, error)` runs (e.g. mark the build failed).
`PermanentJobError` skips the retries.  While a handler runs, its lease is renewed every
lease/3 seconds; if the lease is lost (another worker took over after an expiry) the
handler is cancelled so two workers never keep running the same job.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import traceback
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable

from app.config import get_settings
from app.jobs import queue
from app.jobs.queue import ClaimedJob

log = logging.getLogger(__name__)

Handler = Callable[[ClaimedJob], Awaitable[dict | None]]
DeadHook = Callable[[ClaimedJob, str], Awaitable[None]]


class PermanentJobError(Exception):
    """Raise from a handler to fail the job without further retries."""


@dataclass
class _Entry:
    handler: Handler
    on_dead: DeadHook | None = None


_HANDLERS: dict[str, _Entry] = {}


def register(kind: str, *, on_dead: DeadHook | None = None):
    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = _Entry(fn, on_dead)
        return fn
    return deco


def load_handlers() -> dict[str, _Entry]:
    """Import every module that registers handlers."""
    import app.course_builder.jobs  # noqa: F401
    return _HANDLERS


def new_worker_id(prefix: str = "w") -> str:
    return f"{prefix}:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


async def _dead(job: ClaimedJob, error: str) -> None:
    entry = _HANDLERS.get(job.kind)
    if entry is None or entry.on_dead is None:
        return
    try:
        await entry.on_dead(job, error)
    except Exception:
        log.exception("on_dead hook failed for job %s (%s)", job.id, job.kind)


async def reap_once() -> int:
    dead = await queue.reap_expired()
    for job in dead:
        log.warning("job %s (%s) failed: lease expired with no attempts left", job.id, job.kind)
        await _dead(job, job.meta.get("error") or "lease expired")
    return len(dead)


async def execute(job: ClaimedJob, worker_id: str) -> str:
    """Run one claimed job to completion; returns the final queue status."""
    s = get_settings()
    entry = _HANDLERS.get(job.kind)
    if entry is None:
        status = await queue.fail(job.id, worker_id, f"no handler registered for kind {job.kind!r}", retry=False)
        return status or "lost"

    task = asyncio.ensure_future(entry.handler(job))
    lost = False

    async def _beat() -> None:
        nonlocal lost
        interval = max(0.05, s.jobs_lease_seconds / 3)
        while not task.done():
            await asyncio.sleep(interval)
            if task.done():
                return
            try:
                ok = await asyncio.shield(queue.heartbeat(job.id, worker_id))
            except Exception:
                log.warning("heartbeat failed for job %s", job.id, exc_info=True)
                continue
            if not ok:
                lost = True
                log.error("lease lost for job %s; cancelling local run", job.id)
                task.cancel()
                return

    beat = asyncio.ensure_future(_beat())
    try:
        result = await task
    except asyncio.CancelledError:
        beat.cancel()
        if lost:
            return "lost"
        # worker shutting down: hand the job back (resumes elsewhere / after restart)
        try:
            await asyncio.shield(queue.release(job.id, worker_id))
        except Exception:
            pass
        raise
    except Exception as exc:
        beat.cancel()
        err = f"{type(exc).__name__}: {exc}"
        log.warning("job %s (%s) attempt %d/%d failed: %s", job.id, job.kind, job.attempts, job.max_attempts, err)
        log.debug("%s", traceback.format_exc())
        status = await queue.fail(job.id, worker_id, err, retry=not isinstance(exc, PermanentJobError))
        if status == "failed":
            await _dead(job, err)
        return status or "lost"
    beat.cancel()
    await queue.complete(job.id, worker_id, result if isinstance(result, dict) else None)
    return "succeeded"


async def run_until_idle(*, dedupe_keys: Iterable[str] | None = None, timeout: float = 120.0,
                         worker_id: str | None = None) -> None:
    """Claim and run matching jobs in this event loop until none are queued/running.
    Jobs running in another worker are waited for (polling); delayed retries are awaited."""
    load_handlers()
    keys = list(dedupe_keys) if dedupe_keys is not None else None
    wid = worker_id or new_worker_id("inline")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        await reap_once()
        job = await queue.claim(wid, dedupe_keys=keys)
        if job is not None:
            await execute(job, wid)
            continue
        if keys is not None and not await queue.active_keys(keys):
            return
        if keys is None:
            return
        if loop.time() >= deadline:
            raise asyncio.TimeoutError(f"jobs {keys} still active after {timeout}s")
        await asyncio.sleep(0.05)


class Worker:
    """Polls the queue and runs up to `concurrency` jobs at once."""

    def __init__(self, concurrency: int | None = None, *, kinds: Iterable[str] | None = None,
                 worker_id: str | None = None) -> None:
        s = get_settings()
        self.concurrency = max(1, concurrency or s.jobs_concurrency)
        self.kinds = list(kinds) if kinds is not None else None
        self.worker_id = worker_id or new_worker_id()
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = False
        self._running: set[asyncio.Task] = set()
        self._main: asyncio.Task | None = None

    def wake(self) -> None:
        """Thread-safe: poll now (called by queue.enqueue in this process)."""
        if self._loop is None or self._wake is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._wake.set)
        except RuntimeError:
            pass

    async def run_forever(self) -> None:
        load_handlers()
        s = get_settings()
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        queue.register_waker(self.wake)
        log.info("job worker %s started (concurrency=%d)", self.worker_id, self.concurrency)
        try:
            from app.course_builder.jobs import fail_orphaned_builds
            await fail_orphaned_builds()
        except Exception:
            log.exception("orphaned build sweep failed")
        last_reap = 0.0
        try:
            while not self._stop:
                self._wake.clear()
                now = self._loop.time()
                if now - last_reap >= max(1.0, s.jobs_lease_seconds / 4):
                    last_reap = now
                    try:
                        await reap_once()
                    except Exception:
                        log.exception("reaper failed")
                claimed_any = False
                while len(self._running) < self.concurrency and not self._stop:
                    try:
                        job = await queue.claim(self.worker_id, kinds=self.kinds)
                    except Exception:
                        log.exception("claim failed")
                        job = None
                    if job is None:
                        break
                    claimed_any = True
                    t = asyncio.ensure_future(self._run(job))
                    self._running.add(t)
                    t.add_done_callback(self._done)
                if claimed_any:
                    continue
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=s.jobs_poll_interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            queue.unregister_waker(self.wake)

    def _done(self, t: asyncio.Task) -> None:
        self._running.discard(t)
        if self._wake is not None:
            self._wake.set()

    async def _run(self, job: ClaimedJob) -> None:
        try:
            status = await execute(job, self.worker_id)
            log.info("job %s (%s) -> %s", job.id, job.kind, status)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("job %s crashed the runner", job.id)

    def start(self) -> asyncio.Task:
        self._main = asyncio.ensure_future(self.run_forever())
        return self._main

    async def stop(self, grace: float = 5.0) -> None:
        """Stop polling, give running jobs `grace` seconds, then cancel them (their jobs are
        released back to the queue and resume on the next start)."""
        self._stop = True
        if self._wake is not None:
            self._wake.set()
        if self._main is not None:
            # let the poll loop notice _stop (finishing any in-flight claim query) before
            # resorting to cancellation
            done, _ = await asyncio.wait([self._main], timeout=max(1.0, grace))
            if not done:
                self._main.cancel()
            await asyncio.gather(self._main, return_exceptions=True)
        if self._running:
            await asyncio.wait(list(self._running), timeout=grace)
        for t in list(self._running):
            t.cancel()
        if self._running:
            await asyncio.gather(*self._running, return_exceptions=True)
