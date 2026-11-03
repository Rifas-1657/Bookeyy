"""
tests/test_voice_narration.py — narration spoken through the local TTS router.

MockBookService(narration_tts=True) must send one playable audio_chunk per sentence
(display-word timings with global wordIndex) and still emit every text_chunk once.
"""
from __future__ import annotations

import base64

import pytest

from app.services.mock_book_service import MockBookService
from app.voice import TTSResult, WordTiming

pytestmark = pytest.mark.asyncio


def _fake_result(sentence: str, provider: str = "pocket_tts") -> TTSResult:
    words = sentence.split()
    return TTSResult(
        audio_bytes=b"RIFF" + b"\0" * 64,
        mime_type="audio/wav",
        word_timings=[WordTiming(w, i * 2, i * 2 + 2, word_index=i) for i, w in enumerate(words)],
        duration_ms=len(words) * 2,
        provider=provider,
    )


async def _run_page(monkeypatch, provider: str = "pocket_tts") -> list[dict]:
    calls: list[str] = []

    async def fake_synth(text, voice="", speed=1.0, lang="auto"):
        calls.append(text)
        return _fake_result(text, provider)

    monkeypatch.setattr("app.voice.pipeline.synthesize_segment", fake_synth)
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    svc = MockBookService(send=send, token_ms=1, narration_tts=True)
    await svc.handle_start_page("python", "lesson_python_1_1")
    await svc._narration.task
    return sent


async def test_one_audio_chunk_per_sentence_with_display_timings(monkeypatch):
    sent = await _run_page(monkeypatch)
    audio = [m for m in sent if m["type"] == "audio_chunk"]
    texts = [m for m in sent if m["type"] == "text_chunk"]
    assert audio, "narration should be spoken"
    assert sent[-1]["type"] == "page_complete"

    # Every word is emitted exactly once, in order.
    starts = [m["payload"]["wordStart"] for m in texts]
    assert starts == list(range(len(starts)))

    # Each clip is a whole sentence (final chunk) whose timings point at display words.
    words = [m["payload"]["text"] for m in texts]
    for m in audio:
        p = m["payload"]
        assert p["final"] is True
        assert base64.b64decode(p["base64"]).startswith(b"RIFF")
        for t in p["wordTimings"]:
            assert words[t["wordIndex"]] == t["word"]


async def test_silent_tts_falls_back_to_timed_text(monkeypatch):
    sent = await _run_page(monkeypatch, provider="silent")
    assert not [m for m in sent if m["type"] == "audio_chunk"]
    assert [m for m in sent if m["type"] == "text_chunk"]
    assert sent[-1]["type"] == "page_complete"
