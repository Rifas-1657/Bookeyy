"""
tests/test_voice_space.py — Bookey's own voice service: providers and fallback order.
"""
from __future__ import annotations

import base64

import httpx
import pytest

from app.config import get_settings
from app.voice import pipeline
from app.voice.stt import space_stt
from app.voice.tts import space_tts_provider


def _client_with(handler):
    real = httpx.AsyncClient
    return lambda **kw: real(transport=httpx.MockTransport(handler), **kw)


@pytest.mark.asyncio
async def test_space_tts_returns_audio_and_word_timings(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("X-Voice-Token")
        seen["body"] = request.read()
        return httpx.Response(200, json={"audio_base64": base64.b64encode(b"RIFF....").decode(),
                                         "mime": "audio/wav", "duration_ms": 1200, "sample_rate": 22050})

    monkeypatch.setattr(space_tts_provider.httpx, "AsyncClient", _client_with(handler))
    result = await space_tts_provider.SpaceTTSProvider("https://voice.test", "secret", gender="male").synthesize("vanakkam ellarum")
    assert seen["token"] == "secret" and b'"gender":"male"' in seen["body"]
    assert result.audio_bytes == b"RIFF...." and result.duration_ms == 1200
    assert [t.word for t in result.word_timings] == ["vanakkam", "ellarum"]


@pytest.mark.asyncio
async def test_space_stt_raises_so_callers_can_fall_back(monkeypatch):
    monkeypatch.setattr(space_stt.httpx, "AsyncClient", _client_with(lambda r: httpx.Response(503)))
    with pytest.raises(httpx.HTTPError):
        await space_stt.SpaceSTT("https://voice.test", "secret").transcribe(b"abc", "audio/webm")


def test_voice_service_leads_the_tts_chain_only_when_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "voice_space_url", "")
    assert all(p.name != "space" for p in pipeline.get_tts_chain("tanglish", "female"))
    monkeypatch.setattr(settings, "voice_space_url", "https://voice.test")
    monkeypatch.setattr(settings, "voice_space_token", "secret")
    chain = pipeline.get_tts_chain("tanglish", "female")
    assert chain[0].name == "space" and chain[-1].name == "silent"
