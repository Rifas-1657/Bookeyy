"""
tests/test_voice_local_tts.py — Local free TTS pipeline: transliteration alignment,
cache-hit word timings, router fallback (all engines mocked; runs offline & fast).
"""
from __future__ import annotations

import pytest

from app.voice import TTSResult, WordTiming
from app.voice.audio import PCMAudio
from app.voice.transliterate import (
    align_english,
    align_tanglish,
    align_tanglish_sync,
    detect_language,
    merge_suffix,
    number_to_tamil_script,
    romanize_tamil,
    rule_based_tamil,
    tts_text_of,
)

# /api/voice/* now requires auth; authenticate the unauthenticated test clients.
pytestmark = pytest.mark.usefixtures("voice_user_override")

TAMIL = range(0x0B80, 0x0C00)


def _is_tamil(s: str) -> bool:
    letters = [c for c in s if c.isalpha() or ord(c) in TAMIL]
    return bool(letters) and all(ord(c) in TAMIL for c in letters)


# ---------------------------------------------------------------------------
# Transliteration
# ---------------------------------------------------------------------------

def test_reference_sentence_exact():
    words = align_tanglish_sync("Vanakkam! Innaiku namma Python variables pathi kathukkaporom.")
    assert tts_text_of(words) == "வணக்கம்! இன்னைக்கு நம்ம பைதான் வேரியபிள்ஸ் பத்தி கத்துக்கப்போறோம்."


def test_alignment_is_one_to_one_with_display_tokens():
    text = "React-la useState hook use pannumbodhu, state update aana component re-render aagum."
    words = align_tanglish_sync(text)
    assert [w.display for w in words] == text.split()
    by = {w.display: w.tts for w in words}
    assert by["React-la"] == "ரியாக்ட்ல"
    assert by["useState"] == "யூஸ் ஸ்டேட்"          # one display word → two Tamil words
    assert by["component"] == "காம்போனன்ட்"
    assert by["pannumbodhu,"].endswith(",")          # prosody punctuation kept
    assert all(_is_tamil(w.tts) for w in words)


def test_numbers_symbols_and_code_tokens():
    words = align_tanglish_sync("x = 42 nu ezhudhuna print(x) 3.14 kaattum")
    tts = [w.tts for w in words]
    assert tts[1] == "ஈக்வல்ஸ்"
    assert tts[2] == "ஃபோர்ட்டி டூ"
    assert tts[5] == "பிரிண்ட் எக்ஸ்"
    assert tts[6] == number_to_tamil_script("3.14") == "த்ரீ பாயிண்ட் ஒன் ஃபோர்"
    assert len(words) == 8


def test_offline_rule_fallback_produces_tamil_for_unknown_words():
    words = align_tanglish_sync("Kozhukattai saapidalaam vaanga")
    assert len(words) == 3
    assert all(_is_tamil(w.tts) for w in words)
    assert rule_based_tamil("sollunga") == "சொல்லுங்க"


def test_suffix_sandhi():
    assert merge_suffix("பைதான்", "உம்") == "பைதானும்"
    assert merge_suffix("டேட்டா", "அ") == "டேட்டாவ"
    assert merge_suffix("பைதான்", "க்கு") == "பைதானுக்கு"


async def test_llm_layer_used_and_cached(tmp_path, monkeypatch):
    import app.voice.cache as cache_mod
    import app.voice.transliterate as tr

    monkeypatch.setattr(cache_mod, "_cache_dir", lambda: tmp_path)
    calls = []

    async def fake_llm(sentence, words):
        calls.append(list(words))
        return {w.lower(): "சூப்பர்" for w in words}

    monkeypatch.setattr(tr, "_llm_transliterate", fake_llm)
    text = "Indha zorblax romba nalla"
    w1 = await align_tanglish(text, use_llm=True)
    w2 = await align_tanglish(text, use_llm=True)  # served from disk cache
    assert calls == [["zorblax"]]
    assert w1[1].tts == "சூப்பர்" and w2[1].tts == "சூப்பர்"
    tr._LEARNED.clear()


async def test_llm_failure_falls_back_to_rules(monkeypatch, tmp_path):
    import app.voice.cache as cache_mod
    import app.voice.transliterate as tr

    monkeypatch.setattr(cache_mod, "_cache_dir", lambda: tmp_path)

    async def broken(sentence, words):
        raise TimeoutError

    monkeypatch.setattr(tr, "_llm_transliterate", broken)
    words = await align_tanglish("Indha qwertyx nalla", use_llm=True)
    assert _is_tamil(words[1].tts)


def test_language_detection():
    assert detect_language("Welcome back! Today we will learn how variables store values in Python.") == "english"
    assert detect_language("Vanakkam! Innaiku namma Python variables pathi kathukkaporom.") == "tanglish"
    assert detect_language("React-la useState hook use pannumbodhu") == "tanglish"
    assert detect_language("வணக்கம்") == "tanglish"


def test_english_alignment():
    words = align_english("Set x = 42 then call useState.")
    assert [w.display for w in words] == "Set x = 42 then call useState.".split()
    assert words[2].tts == "equals"
    assert words[3].tts == "forty two"
    assert words[6].tts == "use State."


def test_romanize_tamil_for_stt():
    out = romanize_tamil("வணக்கம்! இன்னைக்கு பைதான் வேரியபிள்ஸ் பத்தி")
    assert out.isascii()
    assert "Python" in out and "variables" in out
    assert romanize_tamil("Already latin text") == "Already latin text"


# ---------------------------------------------------------------------------
# Router / cache (mock engines)
# ---------------------------------------------------------------------------

class FakePCMProvider:
    langs = ("tanglish", "english")
    voices = ()
    default_voice = "fake"

    def __init__(self, name="fake", fail=False):
        self.name = name
        self.fail = fail
        self.calls: list[str] = []

    def pick_voice(self, voice=""):
        return voice or self.default_voice

    async def synthesize_pcm(self, text, voice="", speed=1.0):
        import numpy as np
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("boom")
        sr = 24000
        t = np.arange(int(sr * 0.08 * len(text.split()))) / sr
        tone = (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32")
        pad = np.zeros(int(sr * 0.2), dtype="float32")
        return PCMAudio(samples=np.concatenate([pad, tone, pad]), sample_rate=sr, speed_applied=True)


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    from app.config import get_settings
    import app.voice.cache as cache_mod
    monkeypatch.setattr(get_settings(), "audio_cache_dir", str(tmp_path))
    monkeypatch.setattr(cache_mod, "_cache_dir", lambda: tmp_path)
    return tmp_path


async def test_router_fallback_and_display_timings(tmp_cache, monkeypatch):
    import app.voice.pipeline as pl
    from app.voice.tts.silent import SilentTTS

    bad = FakePCMProvider("pocket_tts", fail=True)
    good = FakePCMProvider("edge_tts")
    monkeypatch.setattr(pl, "get_tts_chain", lambda lang="tanglish", gender="": [bad, good, SilentTTS()])

    text = "Vanakkam! Innaiku namma Python variables pathi kathukkaporom. Sari, aarambikalaam."
    res = await pl.synthesize_segment(text, lang="tanglish")
    assert res.provider == "edge_tts"
    assert bad.calls and good.calls
    assert good.calls[0].startswith("வணக்கம்!")         # engine got Tamil script
    assert len(good.calls) == 3                       # sentence-split (3 sentences)
    assert [t.word for t in res.word_timings] == text.split()   # DISPLAY words
    assert [t.word_index for t in res.word_timings] == list(range(len(text.split())))
    starts = [t.start_ms for t in res.word_timings]
    assert starts == sorted(starts)
    # sentence pause inserted between sentence 1 end and sentence 2 start
    assert res.word_timings[1].start_ms - res.word_timings[0].end_ms >= 300
    assert res.audio_bytes[:4] == b"RIFF"


async def test_cache_hit_returns_word_timings(tmp_cache, monkeypatch):
    import app.voice.pipeline as pl
    from app.voice.tts.silent import SilentTTS

    prov = FakePCMProvider("pocket_tts")
    monkeypatch.setattr(pl, "get_tts_chain", lambda lang="tanglish", gender="": [prov, SilentTTS()])
    text = "React-la useState hook use pannumbodhu, state update aagum."
    first = await pl.synthesize_segment(text, lang="tanglish")
    second = await pl.synthesize_segment(text, lang="tanglish")
    assert len(prov.calls) == 1
    assert second.from_cache is True
    assert second.word_timings, "cache hit must return timings"
    assert [(t.word, t.word_index, t.start_ms, t.end_ms) for t in second.word_timings] == \
        [(t.word, t.word_index, t.start_ms, t.end_ms) for t in first.word_timings]
    assert second.audio_bytes == first.audio_bytes
    assert second.tts_text == first.tts_text


async def test_cache_key_depends_on_provider_lang_and_text():
    from app.voice.pipeline import tts_cache_key
    k = tts_cache_key("வணக்கம்", "pocket_tts", "geetha", "tanglish", 1.0)
    assert k != tts_cache_key("வணக்கம்", "edge_tts", "geetha", "tanglish", 1.0)
    assert k != tts_cache_key("வணக்கம்", "pocket_tts", "geetha", "english", 1.0)
    assert k != tts_cache_key("வணக்கம்!", "pocket_tts", "geetha", "tanglish", 1.0)
    assert k != tts_cache_key("வணக்கம்", "pocket_tts", "geetha", "tanglish", 1.25)


async def test_all_fail_returns_silent_with_display_timings(tmp_cache, monkeypatch):
    import app.voice.pipeline as pl
    from app.voice.tts.silent import SilentTTS

    monkeypatch.setattr(pl, "get_tts_chain", lambda lang="tanglish", gender="": [FakePCMProvider(fail=True), SilentTTS()])
    res = await pl.synthesize_segment("Welcome back to Python.", lang="english")
    assert res.audio_bytes == b""
    assert res.provider == "silent"
    assert [t.word for t in res.word_timings] == ["Welcome", "back", "to", "Python."]


def test_default_chain_is_local_female(monkeypatch):
    import app.voice.pipeline as pl
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "tts_provider", "local")
    ta = [p.name for p in pl.get_tts_chain("tanglish")]
    en = [p.name for p in pl.get_tts_chain("english")]
    assert ta[-1] == "silent" and en[-1] == "silent"
    assert "kokoro" not in ta and "pocket_tts" not in en
    from app.voice.tts.pocket_tts_provider import PocketTTSProvider
    if PocketTTSProvider.is_installed():
        assert ta[0] == "pocket_tts"
    assert s.edge_tts_voice == "ta-IN-PallaviNeural"
    assert s.edge_tts_voice_en == "en-IN-NeerjaNeural"
    assert s.tts_english_voice == "af_heart"


async def test_audio_chunk_payload_shape():
    from app.voice.pipeline import build_audio_chunk_payloads, emit_audio_chunks
    res = TTSResult(audio_bytes=b"x" * 40000, mime_type="audio/wav", duration_ms=900,
                    provider="pocket_tts", lang="tanglish",
                    word_timings=[WordTiming("Vanakkam!", 0, 500, 0, "வணக்கம்!"),
                                  WordTiming("namma", 600, 900, 1, "நம்ம")])
    ps = build_audio_chunk_payloads(res, segment_id="seg-1", seq_start=5, word_offset=10)
    assert [p["seq"] for p in ps] == [5, 6, 7]
    assert ps[-1]["final"] and not ps[0]["final"]
    assert ps[0]["wordTimings"] == [
        {"word": "Vanakkam!", "startMs": 0, "endMs": 500, "wordIndex": 10},
        {"word": "namma", "startMs": 600, "endMs": 900, "wordIndex": 11},
    ]
    assert "wordTimings" not in ps[1]
    from app.contracts.models import AudioChunkPayload
    AudioChunkPayload(**{k: ps[0][k] for k in ("segmentId", "mime", "base64", "seq", "final")})

    sent = []

    async def send(m):
        sent.append(m)

    nxt = await emit_audio_chunks(res, send, seq_start=0, segment_id="s")
    assert nxt == 3 and sent[0]["type"] == "audio_chunk" and "wordTimings" in sent[0]


async def test_voices_endpoint():
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/voice/voices")
    assert r.status_code == 200
    data = r.json()
    ids = {p["id"] for p in data["providers"]}
    assert {"pocket_tts", "kokoro", "edge", "indic_parler"} <= ids
    assert data["defaults"]["tanglish"]["provider"] == "pocket_tts"


async def test_local_tts_endpoint_json(tmp_cache, monkeypatch):
    import app.voice.pipeline as pl
    from httpx import ASGITransport, AsyncClient
    from app.config import get_settings
    from app.main import app
    from app.voice.tts.silent import SilentTTS

    monkeypatch.setattr(get_settings(), "tts_provider", "local")
    monkeypatch.setattr(pl, "get_tts_chain", lambda lang="tanglish", gender="": [FakePCMProvider("kokoro"), SilentTTS()])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/voice/tts?format=json",
                         json={"text": "Welcome back! Today we learn Python.", "lang": "english"})
        r2 = await c.post("/api/voice/tts?format=json",
                          json={"text": "Welcome back! Today we learn Python.", "lang": "english"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["provider"] == "kokoro" and d["lang"] == "english"
    assert [t["word"] for t in d["word_timings"]][:2] == ["Welcome", "back!"]
    assert r.headers["X-Cache"] == "MISS" and r2.headers["X-Cache"] == "HIT"
    assert r2.json()["word_timings"] == d["word_timings"]


async def test_preview_requires_admin():
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/voice/preview", json={"text": "Vanakkam"})
    assert r.status_code in (401, 403)
