"""
app/voice/pipeline.py

Voice pipeline entry points (free, local, female voices by default):

synthesize_segment(text, lang="auto"|"tanglish"|"english", voice, speed) → TTSResult
  Tanglish: transliterate to Tamil script (word-aligned) → pocket_tts (Geetha)
            → edge ta-IN-PallaviNeural → silent
  English:  kokoro (af_heart) → edge en-IN-NeerjaNeural → silent
  - long text is split into sentences (and long sentences at commas); each part
    is synthesized, silence-trimmed and joined with natural pauses
    (~350 ms sentence, ~180 ms comma)
  - word timings are returned for the DISPLAY words (original Latin tokens,
    whitespace split) with `wordIndex`, even on cache hits
  - cache key = sha256(tts_text + provider + voice + lang + style + speed)

synthesize_sentences(text, ...) → async iterator of (sentence, word_offset, TTSResult)
  for streaming narration sentence by sentence.

handle_audio_in(...) → STT → question_received event
emit_audio_chunks(result, ws_send, seq_start, segment_id, word_offset) → audio_chunk events
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from app.voice import SpeechToText, TextToSpeech, TTSResult
    from app.voice.transliterate import AlignedWord


log = logging.getLogger(__name__)

_METRICS: list[dict] = []
SENTENCE_END_RE = re.compile(r'[.!?]["\')\]]?$')
CACHE_STYLE_VERSION = "bookey-local-v2"


def _record(name: str, value_ms: float) -> None:
    _METRICS.append({"metric": name, "value_ms": value_ms, "t": time.time()})
    if len(_METRICS) > 2000:
        del _METRICS[:1000]
    log.debug("voice_metric %s=%.0fms", name, value_ms)


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------

async def get_stt() -> "SpeechToText":
    from app.config import get_settings
    s = get_settings()
    if s.stt_provider.lower() == "groq" and s.groq_api_key:
        from app.voice.stt.groq_whisper import GroqWhisperSTT
        return GroqWhisperSTT(api_key=s.groq_api_key, model=s.bookey_stt_model or s.groq_whisper_model)
    from app.voice.stt.faster_whisper import FasterWhisperSTT
    return FasterWhisperSTT(model_name=s.faster_whisper_model)


# ---------------------------------------------------------------------------
# TTS provider chain
# ---------------------------------------------------------------------------

def _normalize_lang(lang: str) -> str:
    lang = (lang or "").lower()
    if lang in ("en", "english", "en-in", "en-us"):
        return "english"
    if lang in ("ta", "tamil", "tanglish", "ta-in"):
        return "tanglish"
    return "auto"


def make_provider(name: str, lang: str = "tanglish", gender: str = "") -> "TextToSpeech | None":
    """Instantiate a provider by name, or None if it isn't installed/configured.

    *gender* ("male" | "female") picks that tutor voice where the engine has one.
    """
    from app.config import get_settings
    s = get_settings()
    fmt = s.tts_output_format
    name = (name or "").lower()
    try:
        if name in ("pocket_tts", "pocket"):
            from app.voice.tts.pocket_tts_provider import PocketTTSProvider
            return PocketTTSProvider(voice=s.tts_tanglish_voice, output_format=fmt) if PocketTTSProvider.is_installed() else None
        if name == "kokoro":
            from app.voice.tts.kokoro_provider import KokoroTTSProvider
            voice = {"male": s.tts_male_english_voice, "female": s.tts_female_english_voice}.get(gender, s.tts_english_voice)
            return KokoroTTSProvider(voice=voice, output_format=fmt) if KokoroTTSProvider.is_installed() else None
        if name in ("edge", "edge_tts"):
            from app.voice.tts.edge_tts_provider import EdgeTTSProvider
            if not EdgeTTSProvider.is_installed():
                return None
            voices = {
                ("english", "male"): s.edge_tts_voice_en_male, ("english", "female"): s.edge_tts_voice_en_female,
                ("tanglish", "male"): s.edge_tts_voice_male, ("tanglish", "female"): s.edge_tts_voice_female,
            }
            default = s.edge_tts_voice_en if lang == "english" else s.edge_tts_voice
            return EdgeTTSProvider(voice=voices.get((lang, gender), default))
        if name in ("indic_parler", "parler"):
            from app.voice.tts.indic_parler_provider import IndicParlerTTSProvider
            if not IndicParlerTTSProvider.is_installed():
                return None
            return IndicParlerTTSProvider(voice=s.indic_parler_voice, description=s.indic_parler_description,
                                          output_format=fmt)
        if name == "bodhan" and s.bodhan_api_key:
            from app.voice.tts.bodhan_tts import BodhanTTSProvider
            return BodhanTTSProvider(api_key=s.bodhan_api_key, model=s.bodhan_tts_model,
                                     default_voice=s.bodhan_tts_voice, lang=s.bodhan_tts_lang,
                                     style=s.bodhan_tts_style)
        if name == "gemini" and s.gemini_api_key:
            from app.voice.tts.gemini_tts import GeminiTTSProvider
            return GeminiTTSProvider(api_key=s.gemini_api_key, model=s.bookey_tts_model,
                                     default_voice=s.bookey_tts_voice, style=s.bookey_tts_style)
        if name == "space" and s.voice_space_url and s.voice_space_token:
            from app.voice.tts.space_tts_provider import SpaceTTSProvider
            return SpaceTTSProvider(s.voice_space_url, s.voice_space_token, lang=lang, gender=gender)
        if name == "silent":
            from app.voice.tts.silent import SilentTTS
            return SilentTTS()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Could not create TTS provider %s: %s", name, exc)
    return None


def get_tts_chain(lang: str = "tanglish", gender: str = "") -> list["TextToSpeech"]:
    """Ordered provider list for *lang* (and tutor *gender*); SilentTTS is always last."""
    from app.config import get_settings
    from app.voice.tts.silent import SilentTTS
    s = get_settings()
    lang = "english" if _normalize_lang(lang) == "english" else "tanglish"
    # Bookey's own voices first when the voice service is configured; Edge stays the fallback.
    names: list[str] = ["space"] if s.voice_space_url and s.voice_space_token else []
    override = s.tts_provider.lower()
    if override not in ("local", "", "auto"):
        names.append(override)
    gender = gender if gender in ("male", "female") else ""
    if gender:
        names.append(getattr(s, f"tts_{gender}_{lang}_provider"))
    if lang == "english":
        names += [s.tts_english_provider, "edge"]
    else:
        names += [s.tts_tanglish_provider, "edge"]
    providers: list = []
    seen: set[str] = set()
    for n in names:
        n = n.lower()
        if n in seen or n == "silent":
            continue
        seen.add(n)
        p = make_provider(n, lang, gender)
        if p is None:
            continue
        langs = getattr(p, "langs", ("tanglish", "english"))
        if lang not in langs and not (lang == "tanglish" and "tamil" in langs):
            continue
        providers.append(p)
    providers.append(SilentTTS())
    return providers


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _speech_weight(word: str) -> float:
    """Rough spoken-length weight of an engine word (Tamil: syllables; Latin: letters)."""
    core = re.sub(r"[்ா-ௌௗ.,!?;:\-]", "", word)  # drop pulli / vowel signs
    w = max(1.0, float(len(core)))
    if word and word[-1] in ".!?":
        w += 1.5
    elif word and word[-1] in ",;:":
        w += 0.8
    return w


def _split_groups(words: list["AlignedWord"], clause_split_words: int) -> list[tuple[int, int, str]]:
    """Split display tokens into synthesis groups → (start, end, kind) with kind 'sentence'|'clause'."""
    groups: list[tuple[int, int, str]] = []
    start = 0
    n = len(words)
    for i, w in enumerate(words):
        if SENTENCE_END_RE.search(w.display) or i == n - 1:
            groups.append((start, i + 1, "sentence"))
            start = i + 1
    out: list[tuple[int, int, str]] = []
    for gs, ge, kind in groups:
        if ge - gs <= clause_split_words:
            out.append((gs, ge, kind))
            continue
        cs = gs
        for i in range(gs, ge):
            if words[i].display.endswith((",", ";", ":")) and i + 1 - cs >= 6 and ge - (i + 1) >= 4:
                out.append((cs, i + 1, "clause"))
                cs = i + 1
        out.append((cs, ge, kind))
    return out


async def _provider_pcm(provider, text: str, voice: str, speed: float):
    from app.voice.audio import PCMAudio, decode
    if hasattr(provider, "synthesize_pcm"):
        return await provider.synthesize_pcm(text, voice=voice, speed=speed)
    res = await provider.synthesize(text, voice=voice, speed=speed)
    if not res.audio_bytes:
        raise RuntimeError(f"{provider.name} returned empty audio")
    # ffmpeg subprocess → worker thread so the event loop never blocks
    samples, sr = await asyncio.to_thread(decode, res.audio_bytes, res.mime_type)
    return PCMAudio(samples=samples, sample_rate=sr, speed_applied=True)


def _estimate_group_timings(words, start_idx: int, offset_ms: float, dur_ms: float, lead_ms: float = 0.0):
    from app.voice import WordTiming
    weights = [(_speech_weight(w.tts) if w.tts else 0.0) for w in words]
    total = sum(weights) or 1.0
    usable = max(0.0, dur_ms - lead_ms)
    cursor = offset_ms + lead_ms
    out = []
    for k, (w, wt) in enumerate(zip(words, weights)):
        d = usable * wt / total
        out.append(WordTiming(w.display, int(round(cursor)), int(round(cursor + d)), start_idx + k, w.tts))
        cursor += d
    return out


def _boundary_timings(words, start_idx: int, offset_ms: float, boundaries, trim_ms: float, dur_ms: float):
    """Map engine word boundaries (TTS-word space) back to display words, if counts line up."""
    from app.voice import WordTiming
    flat: list[int] = []  # for each engine word → display k
    for k, w in enumerate(words):
        for _ in (w.tts.split() if w.tts else []):
            flat.append(k)
    if not boundaries or len(boundaries) != len(flat):
        return None
    spans: dict[int, list[float]] = {}
    for (bw, bs, be), k in zip(boundaries, flat):
        s_ms = max(0.0, bs - trim_ms)
        e_ms = min(dur_ms, max(s_ms, be - trim_ms))
        cur = spans.setdefault(k, [s_ms, e_ms])
        cur[1] = max(cur[1], e_ms)
    out = []
    last = 0.0
    for k, w in enumerate(words):
        if k in spans:
            s_ms, e_ms = spans[k]
            last = e_ms
        else:
            s_ms = e_ms = last
        out.append(WordTiming(w.display, int(offset_ms + s_ms), int(offset_ms + e_ms), start_idx + k, w.tts))
    return out


async def render_aligned(provider, words: list["AlignedWord"], voice: str, speed: float):
    """Synthesize aligned words with *provider* → (samples, sr, word_timings)."""
    import numpy as np

    from app.config import get_settings
    from app.voice.audio import normalize_peak, resample, silence, time_stretch, trim_silence_bounds

    s = get_settings()
    groups = _split_groups(words, max(6, int(s.tts_clause_split_words)))
    sr_out = 24000
    pieces = []
    timings = []
    offset_ms = 0.0
    for gi, (gs, ge, kind) in enumerate(groups):
        gwords = words[gs:ge]
        text = " ".join(w.tts for w in gwords if w.tts).strip()
        if not text:
            timings += _estimate_group_timings(gwords, gs, offset_ms, 0.0)
            continue
        pcm = await _provider_pcm(provider, text, voice, speed)
        samples = resample(pcm.samples, pcm.sample_rate, sr_out)
        trimmed, start = trim_silence_bounds(samples, sr_out)
        trim_ms = start * 1000.0 / sr_out
        if not pcm.speed_applied and abs(speed - 1.0) >= 0.02:
            trimmed = await asyncio.to_thread(time_stretch, trimmed, sr_out, speed)
        dur_ms = len(trimmed) * 1000.0 / sr_out
        gt = None
        if pcm.boundaries and pcm.speed_applied:
            gt = _boundary_timings(gwords, gs, offset_ms, pcm.boundaries, trim_ms, dur_ms)
        if gt is None:
            gt = _estimate_group_timings(gwords, gs, offset_ms, dur_ms, lead_ms=min(40.0, dur_ms * 0.05))
        timings += gt
        pieces.append(trimmed)
        offset_ms += dur_ms
        if gi < len(groups) - 1:
            pause = s.tts_sentence_pause_ms if kind == "sentence" else s.tts_comma_pause_ms
            pause = int(pause / max(0.5, speed))
            pieces.append(silence(pause, sr_out))
            offset_ms += pause
    if not pieces:
        raise RuntimeError("nothing to synthesize")
    audio = normalize_peak(np.concatenate(pieces))
    return audio, sr_out, timings


def _timings_from_meta(meta: dict):
    from app.voice import WordTiming
    out = []
    for i, t in enumerate(meta.get("timings", [])):
        out.append(WordTiming(t.get("word", ""), int(t.get("startMs", 0)), int(t.get("endMs", 0)),
                              int(t.get("wordIndex", i)), t.get("ttsWord", "")))
    return out


def tts_cache_key(tts_text: str, provider: str, voice: str, lang: str, speed: float) -> str:
    from app.config import get_settings
    from app.voice.cache import compute_voice_cache_key
    s = get_settings()
    style = (f"{CACHE_STYLE_VERSION}|{s.tts_output_format}|p{s.tts_sentence_pause_ms}"
             f"|c{s.tts_comma_pause_ms}|k{s.tts_clause_split_words}")
    return compute_voice_cache_key(text=tts_text, model=provider, voice=voice, lang=lang,
                                   style=style, speed=speed)


async def _silent_result(words, lang: str, tts_text: str):
    from app.voice import TTSResult, WordTiming
    from app.voice.tts.silent import SilentTTS
    display = " ".join(w.display for w in words)
    res = await SilentTTS().synthesize(display or tts_text)
    res.word_timings = [WordTiming(t.word, t.start_ms, t.end_ms, i, words[i].tts if i < len(words) else "")
                        for i, t in enumerate(res.word_timings)]
    res.lang = lang
    res.tts_text = tts_text
    return res


async def synthesize_segment(
    text: str,
    voice: str = "",
    speed: float = 1.0,
    lang: str = "auto",
    provider: str | None = None,
    use_cache: bool = True,
    gender: str = "",
) -> "TTSResult":
    """
    Synthesize *text* (display text, Latin letters) with caching and provider fallback.

    Returns TTSResult whose word_timings are DISPLAY words with word_index set,
    plus tts_text (what the engine read) and lang. Never raises for provider
    failures — the last resort is SilentTTS (empty audio, estimated timings).
    """
    from app.config import get_settings
    from app.voice import TTSResult
    from app.voice.audio import encode
    from app.voice.cache import get_key_lock, get_voice_cache, set_voice_cache
    from app.voice.transliterate import align_text, tts_text_of

    s = get_settings()
    lang_req = _normalize_lang(lang if lang and lang != "auto" else s.tts_default_lang)
    lang_res, words = await align_text(text, lang_req)
    tts_text = tts_text_of(words)
    if not tts_text:
        return await _silent_result(words, lang_res, tts_text)

    if provider:
        p = make_provider(provider, lang_res, gender)
        from app.voice.tts.silent import SilentTTS
        chain = ([p] if p is not None else []) + [SilentTTS()]
    else:
        chain = get_tts_chain(lang_res, gender)

    t0 = time.monotonic()
    for prov in chain:
        if prov.name == "silent":
            break
        pv = prov.pick_voice(voice)
        key = tts_cache_key(tts_text, prov.name, pv, lang_res, speed)
        if use_cache:
            cached = get_voice_cache(key)
            if cached is not None:
                audio, meta = cached
                timings = _timings_from_meta(meta)
                return TTSResult(audio_bytes=audio, mime_type=meta.get("mime_type", "audio/wav"),
                                 word_timings=timings, duration_ms=int(meta.get("duration_ms", 0)),
                                 provider=meta.get("provider", prov.name), from_cache=True,
                                 lang=lang_res, tts_text=tts_text, voice=pv,
                                 sample_rate=int(meta.get("sample_rate", 0)))
        lock = await get_key_lock(key)
        async with lock:
            if use_cache:
                cached = get_voice_cache(key)
                if cached is not None:
                    audio, meta = cached
                    return TTSResult(audio_bytes=audio, mime_type=meta.get("mime_type", "audio/wav"),
                                     word_timings=_timings_from_meta(meta),
                                     duration_ms=int(meta.get("duration_ms", 0)),
                                     provider=meta.get("provider", prov.name), from_cache=True,
                                     lang=lang_res, tts_text=tts_text, voice=pv)
            try:
                samples, sr, timings = await render_aligned(prov, words, pv, speed)
            except Exception as exc:
                log.warning("TTS provider %s failed: %s", prov.name, exc)
                continue
            audio, mime = await asyncio.to_thread(encode, samples, sr, s.tts_output_format)
            duration_ms = int(len(samples) * 1000 / sr)
            latency = (time.monotonic() - t0) * 1000
            _record("tts_latency_ms", latency)
            log.info("TTS %s/%s lang=%s: %d bytes, %dms audio, %.0fms compute (RTF %.2f)",
                     prov.name, pv, lang_res, len(audio), duration_ms, latency,
                     latency / max(1, duration_ms))
            if use_cache:
                try:
                    set_voice_cache(key, audio, {
                        "text": text, "tts_text": tts_text, "provider": prov.name, "model": prov.name,
                        "voice": pv, "lang": lang_res, "speed": speed, "duration_ms": duration_ms,
                        "mime_type": mime, "sample_rate": sr,
                        "timings": [dict(t.to_dict(), ttsWord=t.tts_word) for t in timings],
                    })
                except Exception:
                    log.warning("TTS cache write failed", exc_info=True)
            return TTSResult(audio_bytes=audio, mime_type=mime, word_timings=timings,
                             duration_ms=duration_ms, provider=prov.name, lang=lang_res,
                             tts_text=tts_text, voice=pv, sample_rate=sr)

    return await _silent_result(words, lang_res, tts_text)


def split_sentences(text: str) -> list[tuple[str, int]]:
    """Split display text into sentences (frontend-identical) → [(sentence, word_offset)]."""
    tokens = (text or "").split()
    out: list[tuple[str, int]] = []
    start = 0
    for i, tok in enumerate(tokens):
        if SENTENCE_END_RE.search(tok) or i == len(tokens) - 1:
            out.append((" ".join(tokens[start:i + 1]), start))
            start = i + 1
    return out


async def synthesize_sentences(
    text: str, voice: str = "", speed: float = 1.0, lang: str = "auto",
) -> AsyncIterator[tuple[str, int, "TTSResult"]]:
    """Yield (sentence, word_offset, result) per sentence — each sentence cached separately.

    Global display index of a timing = word_offset + timing.word_index.
    Language is detected per sentence when lang="auto".
    """
    for sentence, offset in split_sentences(text):
        res = await synthesize_segment(sentence, voice=voice, speed=speed, lang=lang)
        yield sentence, offset, res


async def pre_synthesize_page(narration: str, voice: str = "", speed: float = 1.0, lang: str = "auto") -> int:
    """Warm the cache for every sentence of a page narration. Returns sentence count."""
    n = 0
    async for _ in synthesize_sentences(narration, voice=voice, speed=speed, lang=lang):
        n += 1
    return n


# ---------------------------------------------------------------------------
# STT entry point
# ---------------------------------------------------------------------------

async def handle_audio_in(
    session_id: str,
    audio_bytes: bytes,
    mime: str,
    user_id: str,
    course_id: str,
    chapter: int,
    page: int,
    ws_send,  # callable: async (dict) -> None
    narration_active: bool = False,
    llm_router=None,
) -> None:
    """
    Handle an audio_in frame:
      1. STT transcription (transcript is always Latin script / Tanglish)
      2. Emit question_received WS event (source=voice)
    """
    t0 = time.monotonic()
    try:
        stt = await get_stt()
        result = await stt.transcribe(audio_bytes, mime)
        stt_latency = (time.monotonic() - t0) * 1000
        _record("stt_latency_ms", stt_latency)
        log.info("STT %s: %r lang=%s dur=%.1fs", stt.name, result.transcript, result.language, result.duration_s)
    except ValueError as exc:
        await ws_send({"type": "error", "message": f"Audio rejected: {exc}"})
        return
    except Exception as exc:
        log.error("STT failed: %s", exc)
        await ws_send({"type": "error", "message": "Speech recognition failed. Please try text input."})
        return

    if not result.transcript.strip():
        await ws_send({"type": "error", "message": "Unrecognised audio. Please speak clearly."})
        return

    await ws_send({
        "type": "question_received",
        "question": result.transcript,
        "source": "voice",
        "sessionId": session_id,
    })


# ---------------------------------------------------------------------------
# WS audio_chunk framing
# ---------------------------------------------------------------------------

CHUNK_SIZE = 16384


def build_audio_chunk_payloads(
    result: "TTSResult",
    segment_id: str = "",
    seq_start: int = 0,
    word_offset: int = 0,
    chunk_size: int = CHUNK_SIZE,
) -> list[dict]:
    """
    AudioChunkPayload-compatible dicts (segmentId, mime, base64, seq, final) plus,
    on the FIRST chunk only: wordTimings (display words, global wordIndex),
    wordOffset, durationMs, lang, provider.
    """
    audio = result.audio_bytes or b""
    payloads: list[dict] = []
    n = max(1, (len(audio) + chunk_size - 1) // chunk_size)
    for k in range(n):
        chunk = audio[k * chunk_size:(k + 1) * chunk_size]
        p = {
            "segmentId": segment_id,
            "mime": result.mime_type,
            "base64": base64.b64encode(chunk).decode(),
            "seq": seq_start + k,
            "final": k == n - 1,
        }
        if k == 0:
            p["wordTimings"] = [t.to_dict(word_offset) for t in result.word_timings]
            p["wordOffset"] = word_offset
            p["durationMs"] = result.duration_ms
            p["lang"] = result.lang
            p["provider"] = result.provider
        payloads.append(p)
    return payloads


async def emit_audio_chunks(
    result: "TTSResult",
    ws_send,
    seq_start: int = 0,
    segment_id: str = "",
    word_offset: int = 0,
) -> int:
    """Send audio_chunk WS events; returns the next free seq number."""
    seq = seq_start
    for p in build_audio_chunk_payloads(result, segment_id, seq_start, word_offset):
        msg = {"type": "audio_chunk", "seq": p["seq"], "payload": p,
               # legacy flat fields (pre-contract clients)
               "data": p["base64"], "mime": p["mime"], "final": p["final"]}
        if "wordTimings" in p:
            msg["wordTimings"] = p["wordTimings"]
        await ws_send(msg)
        seq = p["seq"] + 1
        await asyncio.sleep(0)
    return seq


def get_metrics() -> list[dict]:
    return list(_METRICS)
