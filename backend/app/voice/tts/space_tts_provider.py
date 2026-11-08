"""
app/voice/tts/space_tts_provider.py — Bookey's own voices from the voice service (tools/voice-space).

Piper on a free CPU Space: a sentence comes back in well under a second. The service returns
audio only, so word timings are estimated from the text like the silent fallback does.
Configured by VOICE_SPACE_URL + VOICE_SPACE_TOKEN; any failure lets the chain move on to Edge.
"""
from __future__ import annotations

import base64

import httpx

from app.voice import TextToSpeech, TTSResult, WordTiming
from app.voice.normalizer import estimate_word_timings

TIMEOUT_S = 12.0


class SpaceTTSProvider(TextToSpeech):
    name = "space"

    def __init__(self, url: str, token: str, lang: str = "tanglish", gender: str = "") -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._lang = lang
        self._gender = gender or "female"

    async def synthesize(self, text: str, voice: str = "", speed: float = 1.0) -> TTSResult:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            resp = await client.post(f"{self._url}/tts", headers={"X-Voice-Token": self._token},
                                     json={"text": text, "lang": self._lang, "gender": self._gender, "speed": speed})
        resp.raise_for_status()
        body = resp.json()
        duration = int(body["duration_ms"])
        timings = [WordTiming(w["word"], w["startMs"], w["endMs"]) for w in estimate_word_timings(text, duration)]
        return TTSResult(audio_bytes=base64.b64decode(body["audio_base64"]), mime_type=body.get("mime", "audio/wav"),
                         word_timings=timings, duration_ms=duration, provider=self.name,
                         voice=f"bookey_{self._gender}", sample_rate=int(body.get("sample_rate") or 0))
