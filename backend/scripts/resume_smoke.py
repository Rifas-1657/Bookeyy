"""
scripts/resume_smoke.py — live check that a learner book build survives a server restart.

  start <prompt>   submit a prompt book as the dev admin, print the job id, wait until lessons are generating
  watch <job_id>   poll until ready/failed and print each status change (+ lessons done)

Run `start`, restart the API while lessons are generating, then run `watch`.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
API = os.getenv("SMOKE_API", "http://127.0.0.1:8000")


async def headers() -> dict:
    from sqlalchemy import select
    from app.auth.service import create_access_token
    from app.db.database import AsyncSessionLocal
    from app.db.models import User

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == "admin@bookey.dev"))).scalar_one()
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


def summary(snap: dict) -> str:
    lessons = snap.get("lessons") or []
    done = sum(1 for l in lessons if l.get("status") == "done")
    return f"{snap['status']:<11} {snap['progress']:>3}%  lessons {done}/{len(lessons)}  busy={snap.get('busy')}"


async def start(prompt: str) -> None:
    async with httpx.AsyncClient(base_url=API, headers=await headers(), timeout=60) as http:
        r = await http.post("/api/books", json={"prompt": prompt, "level": "beginner", "language": "tanglish"})
        r.raise_for_status()
        job = r.json()["jobId"]
        print("job", job)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 400:
            snap = (await http.get(f"/api/books/jobs/{job}")).json()
            lessons = snap.get("lessons") or []
            done = sum(1 for l in lessons if l.get("status") == "done")
            print(f"[{time.monotonic() - t0:4.0f}s] {summary(snap)}", flush=True)
            if snap["status"] == "generating" and 1 <= done < len(lessons):
                print("READY_TO_RESTART", job)
                return
            if snap["status"] in ("ready", "failed"):
                print("finished before restart:", snap["status"])
                return
            await asyncio.sleep(3)


async def watch(job: str) -> None:
    last = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < 900:
        try:
            async with httpx.AsyncClient(base_url=API, headers=await headers(), timeout=30) as http:
                snap = (await http.get(f"/api/books/jobs/{job}")).json()
        except httpx.HTTPError:
            await asyncio.sleep(3)
            continue
        line = summary(snap)
        if line != last:
            print(f"[{time.monotonic() - t0:4.0f}s] {line}", flush=True)
            last = line
        if snap["status"] in ("ready", "failed") and not snap.get("busy"):
            print("FINAL", snap["status"], snap.get("error") or "")
            return
        await asyncio.sleep(3)


if __name__ == "__main__":
    cmd, arg = sys.argv[1], sys.argv[2]
    asyncio.run(start(arg) if cmd == "start" else watch(arg))
