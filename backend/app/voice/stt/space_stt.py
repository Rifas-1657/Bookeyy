"""
app/voice/stt/space_stt.py — Tanglish speech recognition from the voice service (tools/voice-space).

A whisper-small fine-tuned on Tanglish speakers, far more accurate on Tanglish than Groq's
general model (see tools/voice-eval/out/stt_eval.json). Raises on any failure so the caller
can fall back to Groq.
"""
from __future__ import annotations

import httpx

from app.voice import SpeechToText, STTResult

TIMEOUT_S = 20.0


class SpaceSTT(SpeechToText):
    name = "space"

    def __init__(self, url: str, token: str, context: str = "") -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._context = context

    async def transcribe(self, audio_bytes: bytes, mime: str, hint_lang: str | None = None) -> STTResult:
        if not audio_bytes:
            raise ValueError("Audio data is empty")
        if len(audio_bytes) > self.MAX_BYTES:
            raise ValueError(f"Audio too large: {len(audio_bytes)} bytes (max {self.MAX_BYTES})")
        ext = mime.split("/")[-1].split(";")[0] or "webm"
        data = {k: v for k, v in (("language", hint_lang), ("context", self._context)) if v}
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            resp = await client.post(f"{self._url}/stt", headers={"X-Voice-Token": self._token},
                                     files={"file": (f"audio.{ext}", audio_bytes, mime)}, data=data)
        resp.raise_for_status()
        body = resp.json()
        return STTResult(transcript=body.get("transcript", ""), language=body.get("language") or (hint_lang or "ta"),
                         duration_s=float(body.get("duration_s") or 0.0))
