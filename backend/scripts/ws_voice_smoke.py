"""
scripts/ws_voice_smoke.py — live check that /ws/book narration speaks with the local female voice.

Issues a WS ticket for the dev admin from the local DB,
starts a page and reports audio_chunk / text_chunk counts plus the first clip's timings.
Saves the first spoken sentence to .cache/voice-samples/ws_first_sentence.<ext>.

Usage: .venv/Scripts/python scripts/ws_voice_smoke.py [page_id] [seconds]
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import websockets
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
API = os.getenv("SMOKE_API", "http://127.0.0.1:8000")
WS = API.replace("http", "ws", 1) + "/ws/book"


async def main(page_id: str, seconds: float) -> int:
    # Issue a WS ticket for the dev admin straight from the DB (same as the test suite).
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from sqlalchemy import select
    from app.auth.ticket import issue_ticket
    from app.db.database import AsyncSessionLocal
    from app.db.models import User

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == "admin@bookey.dev"))).scalar_one()
        ticket = await issue_ticket(db, user_id=user.id, origin="")
        await db.commit()

    counts: dict[str, int] = {}
    missing_v: set[str] = set()
    first_audio = None
    t0 = time.monotonic()
    first_audio_s = None
    async with websockets.connect(WS, max_size=16 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "auth_ticket", "payload": {"ticket": ticket}}))
        print("auth:", json.loads(await ws.recv()).get("type"))
        await ws.send(json.dumps({"v": 2, "type": "start_page", "payload": {"courseId": "python", "pageId": page_id}}))
        deadline = t0 + seconds
        while time.monotonic() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic())))
            except asyncio.TimeoutError:
                break
            kind = msg.get("type")
            counts[kind] = counts.get(kind, 0) + 1
            if "v" not in msg:
                missing_v.add(kind)
            if kind == "audio_chunk" and first_audio is None:
                first_audio = msg["payload"]
                first_audio_s = time.monotonic() - t0
            if kind == "error":
                print("error:", msg.get("payload"))
            if kind == "page_complete":
                break

    print("events:", counts)
    print("frames without protocol 'v':", sorted(missing_v) or "none")
    if not first_audio:
        print("NO AUDIO")
        return 1
    audio = base64.b64decode(first_audio["base64"])
    ext = "wav" if "wav" in first_audio["mime"] else "mp3"
    out = ROOT / ".cache" / "voice-samples" / f"ws_first_sentence.{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    print(f"first audio after {first_audio_s:.1f}s: {len(audio)} bytes {first_audio['mime']} "
          f"provider={first_audio.get('provider')} lang={first_audio.get('lang')} durationMs={first_audio.get('durationMs')}")
    print("timings:", " ".join(f"{t['word']}@{t['startMs']}" for t in (first_audio.get("wordTimings") or [])[:10]))
    print("saved:", out)
    return 0


if __name__ == "__main__":
    page = sys.argv[1] if len(sys.argv) > 1 else "lesson_python_1_1"
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 25
    sys.exit(asyncio.run(main(page, secs)))
