"""
app/course_builder/sse.py — Server-Sent Events stream of a course-build job snapshot.

Shared by the admin builder (/api/admin/course-builder/jobs/{id}/stream) and the
learner flow (/api/books/jobs/{id}/stream).  Each `event: job` carries the same
JSON as GET .../jobs/{id}; `event: end` fires once the job is terminal and idle.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator, Callable

from fastapi.responses import StreamingResponse

from app.course_builder import runner
from app.course_builder.settings import get_cb_settings



def job_event_stream(job_id: str, decorate: Callable[[dict], dict] | None = None) -> StreamingResponse:
    cb = get_cb_settings()

    async def gen() -> AsyncGenerator[str, None]:
        last = None
        elapsed = 0.0
        idle_ticks = 0
        while elapsed < cb.sse_max_seconds:
            snap = await runner.snapshot(job_id)
            if snap is None:
                yield "event: error\ndata: {\"detail\": \"job deleted\"}\n\n"
                return
            snap.pop("updatedAt", None)
            if decorate is not None:
                snap = decorate(snap)
            payload = json.dumps(snap, default=str)
            if payload != last:
                last = payload
                yield f"event: job\ndata: {payload}\n\n"
                idle_ticks = 0
            else:
                idle_ticks += 1
                if idle_ticks % 15 == 0:
                    yield ": keep-alive\n\n"
            if snap["status"] in runner.TERMINAL and not snap["busy"]:
                yield f"event: end\ndata: {json.dumps({'status': snap['status']})}\n\n"
                return
            await asyncio.sleep(cb.sse_interval_s)
            elapsed += cb.sse_interval_s

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
