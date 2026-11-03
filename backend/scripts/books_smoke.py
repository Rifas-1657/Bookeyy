"""
scripts/books_smoke.py — live end-to-end check of the learner "Create a book" API.

Mints a short-lived access token for the local dev admin (no password needed), submits a
prompt-only book to the running API, follows the job until chapter 1 is readable, and
prints the outline plus the first page. Uses the real LLM providers from .env.

Usage: .venv/Scripts/python scripts/books_smoke.py ["Teach me SQL joins in Tanglish"] [timeout_s]
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


async def main(prompt: str, timeout_s: float) -> int:
    from sqlalchemy import select
    from app.auth.service import create_access_token
    from app.db.database import AsyncSessionLocal
    from app.db.models import User

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == "admin@bookey.dev"))).scalar_one()
    headers = {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}

    async with httpx.AsyncClient(base_url=API, headers=headers, timeout=60) as http:
        r = await http.post("/api/books", json={"prompt": prompt, "level": "beginner", "language": "tanglish"})
        print("create:", r.status_code, r.json())
        r.raise_for_status()
        job_id, course_id = r.json()["jobId"], r.json()["courseId"]

        t0, last = time.monotonic(), None
        while time.monotonic() - t0 < timeout_s:
            snap = (await http.get(f"/api/books/jobs/{job_id}")).json()
            line = f"{snap['status']:<11} {snap['progress']:>3}%  " + " | ".join(
                f"{s['label']}:{s['status']}" for s in snap["steps"] if s["status"] != "pending")
            if line != last:
                print(f"[{time.monotonic() - t0:5.0f}s] {line}")
                last = line
            if snap.get("bookReady") or snap["status"] in ("ready", "failed"):
                break
            await asyncio.sleep(3)

        if snap["status"] == "failed":
            print("FAILED:", snap.get("error"))
            return 1
        outline = snap.get("outline") or {}
        for ch in outline.get("chapters", []):
            print(f"CH {ch['number']}: {ch['title']}  (project: {ch.get('project', {}).get('title')})")
            for lesson in ch["lessons"]:
                print(f"   - {lesson['title']}")
        pages = (await http.get(f"/api/courses/{course_id}/pages")).json()
        print(f"pages readable now: {len(pages)}")
        if pages:
            first = pages[0]
            print("first page:", first.get("title"))
            print("narration:", (first.get("narration_text") or first.get("narrationText") or "")[:300])
        print(f"open: http://127.0.0.1:3000/book/{course_id}")
    return 0


if __name__ == "__main__":
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Teach me SQL joins for beginners, in Tanglish"
    sys.exit(asyncio.run(main(prompt, float(sys.argv[2]) if len(sys.argv) > 2 else 600)))
