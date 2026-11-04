"""
app/api/voice.py — REST endpoints for Text-to-Speech (TTS) and Speech-to-Text (STT).

Endpoints:
- POST /api/voice/tts:     Synthesize narration text. Default TTS_PROVIDER=local -> free local
                           female voices (Tanglish: Pocket TTS Geetha via Tamil-script
                           transliteration; English: Kokoro af_heart; Edge female fallback).
                           TTS_PROVIDER=bodhan|gemini keeps the legacy cloud path.
- POST /api/voice/stt:     Transcribe audio (local faster-whisper, or Groq Whisper). Latin output.
- GET  /api/voice/voices:  Providers / voices and whether each is installed.
- POST /api/voice/preview: Admin audition of a specific provider/voice (JSON with timings).
- POST /api/voice/intent:  Classify an ambiguous lesson command (stop / next page / quiz me …)
                           with one tiny JSON LLM call; falls back to "question".
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status

from app.auth.dependencies import get_current_user, require_admin
from app.db.models import User
from app.middleware.rate_limit import limiter, voice_limit
from pydantic import BaseModel, Field

from app.config import get_settings
from app.voice.cache import (
    compute_voice_cache_key,
    get_key_lock,
    get_voice_cache,
    set_voice_cache,
)
from app.voice.normalizer import normalize_for_tts
from app.voice.stt.faster_whisper import FasterWhisperSTT
from app.voice.stt.groq_whisper import DEFAULT_TANG_VOCAB_HINT, SUPPORTED_AUDIO_EXTENSIONS, GroqWhisperSTT
from app.voice.tts.bodhan_tts import BodhanTTSProvider
from app.voice.tts.gemini_tts import GeminiTTSProvider

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

MAX_TTS_TEXT_LENGTH = 5000
MAX_STT_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB


class TTSRequest(BaseModel):
    text: str = Field(
        ...,
        min_length=1,
        max_length=MAX_TTS_TEXT_LENGTH,
        description="Narration text to synthesize",
    )
    voice: str | None = Field(default=None, description="Optional voice identifier")
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="Playback speed (0.5 - 2.0)")
    lang: str | None = Field(default=None, description="'tanglish' | 'english' | 'auto' (default auto)")
    provider: str | None = Field(default=None, description="Force a provider (local mode only)")
    gender: str | None = Field(default=None, description="Tutor voice the learner picked: 'male' | 'female'")


class TTSResponseJSON(BaseModel):
    audio_base64: str
    mime_type: str
    duration_ms: int
    word_timings: list[dict] | None = None
    provider: str | None = None
    lang: str | None = None
    tts_text: str | None = None
    from_cache: bool | None = None


class PreviewRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    lang: str | None = Field(default="auto")
    provider: str | None = Field(default=None, description="pocket_tts | kokoro | edge | indic_parler ...")
    voice: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class STTResponse(BaseModel):
    transcript: str
    language: str
    duration_s: float


# ---------------------------------------------------------------------------
# TTS: Text-to-Speech
# ---------------------------------------------------------------------------

class WarmRequest(BaseModel):
    courseId: str
    pageId: str
    gender: str = ""


_warming: set[asyncio.Task] = set()


@router.post("/warm", status_code=202, summary="Prepare a page's first sentences before Play")
@limiter.limit(voice_limit)
async def warm_page(request: Request, req: WarmRequest, user: User = Depends(get_current_user)) -> dict:
    """A cold synthesis takes 4-6 s. Doing it while the learner reads the page makes Play instant."""
    from app.books.access import user_can_view_course
    from app.db.database import AsyncSessionLocal
    from app.db.models import Page
    from app.voice.pipeline import split_sentences, synthesize_segment

    if not await user_can_view_course(req.courseId, user.id):
        raise HTTPException(status_code=404, detail="Page not found")
    async with AsyncSessionLocal() as db:
        page = await db.get(Page, req.pageId)
    if page is None or page.course_id != req.courseId:
        raise HTTPException(status_code=404, detail="Page not found")
    for text, _ in split_sentences(page.narration_text)[:2]:
        task = asyncio.create_task(synthesize_segment(text, lang="auto", gender=req.gender))
        _warming.add(task)
        task.add_done_callback(_warming.discard)
    return {"status": "warming"}


@router.post("/tts", summary="Synthesize speech from lesson narration text")
@limiter.limit(voice_limit)
async def text_to_speech(
    request: Request,
    req: TTSRequest,
    format: str = Query("audio", description="Response format: 'audio' (binary) or 'json'"),
    user: User = Depends(get_current_user),
) -> Response:
    """
    Synthesize lesson narration text. Auth required; rate-limited per user.
    Checks persistent disk cache first (0 API calls on cache hit).
    On cache miss, synthesizes via Bodhan Indic-Speak (or Gemini) and caches atomically.
    The `provider` override is honoured for admins only (it can select paid engines).
    """
    if req.provider and getattr(user, "role", None) != "admin":
        req = req.model_copy(update={"provider": None})
    cleaned_text = normalize_for_tts(req.text).strip()
    if not cleaned_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Narration text contains no pronounceable content",
        )

    settings = get_settings()
    provider_name = settings.tts_provider.lower()

    if provider_name not in ("bodhan", "gemini"):
        return await _local_tts(req, format)

    # Determine provider parameters for cache key computation
    if provider_name == "bodhan":
        model = settings.bodhan_tts_model
        voice = req.voice or settings.bodhan_tts_voice
        lang = settings.bodhan_tts_lang
        style = settings.bodhan_tts_style
    elif provider_name == "gemini":
        model = settings.bookey_tts_model
        voice = req.voice or settings.bookey_tts_voice
        lang = "ta"
        style = settings.bookey_tts_style
    else:
        model = "edge"
        voice = req.voice or settings.edge_tts_voice
        lang = "ta"
        style = "default"

    cache_key = compute_voice_cache_key(
        text=cleaned_text,
        model=model,
        voice=voice,
        lang=lang,
        style=style,
        speed=req.speed,
    )

    # 1. Fast path: check persistent cache
    cached = get_voice_cache(cache_key)
    if cached is not None:
        audio_bytes, meta = cached
        mime_type = meta.get("mime_type", "audio/wav")
        duration_ms = meta.get("duration_ms", 0)

        if format == "json":
            json_data = TTSResponseJSON(
                audio_base64=base64.b64encode(audio_bytes).decode("ascii"),
                mime_type=mime_type,
                duration_ms=duration_ms,
            )
            return Response(
                content=json_data.model_dump_json(),
                media_type="application/json",
                headers={"X-Cache": "HIT"},
            )

        return Response(
            content=audio_bytes,
            media_type=mime_type,
            headers={
                "Content-Disposition": "inline; filename=speech.wav",
                "Cache-Control": "public, max-age=86400",
                "X-Cache": "HIT",
            },
        )

    # 2. Slow path: Acquire per-key lock to coordinate concurrent requests for same audio
    lock = await get_key_lock(cache_key)
    async with lock:
        # Re-check cache in case another request populated it while waiting
        cached = get_voice_cache(cache_key)
        if cached is not None:
            audio_bytes, meta = cached
            mime_type = meta.get("mime_type", "audio/wav")
            duration_ms = meta.get("duration_ms", 0)

            if format == "json":
                json_data = TTSResponseJSON(
                    audio_base64=base64.b64encode(audio_bytes).decode("ascii"),
                    mime_type=mime_type,
                    duration_ms=duration_ms,
                )
                return Response(
                    content=json_data.model_dump_json(),
                    media_type="application/json",
                    headers={"X-Cache": "HIT"},
                )

            return Response(
                content=audio_bytes,
                media_type=mime_type,
                headers={
                    "Content-Disposition": "inline; filename=speech.wav",
                    "Cache-Control": "public, max-age=86400",
                    "X-Cache": "HIT",
                },
            )

        # Synthesize via provider
        if not settings.voice_enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Voice service is disabled",
            )

        if provider_name == "bodhan":
            if not settings.bodhan_api_key:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Bodhan TTS service is not configured (missing BODHAN_API_KEY)",
                )
            provider = BodhanTTSProvider(
                api_key=settings.bodhan_api_key,
                model=model,
                default_voice=voice,
                lang=lang,
                style=style,
            )
        elif provider_name == "gemini":
            if not settings.gemini_api_key:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Gemini TTS service is not configured (missing API key)",
                )
            provider = GeminiTTSProvider(
                api_key=settings.gemini_api_key,
                model=model,
                default_voice=voice,
                style=style,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"TTS provider '{provider_name}' is not configured",
            )

        try:
            result = await provider.synthesize(
                text=cleaned_text,
                voice=voice,
                speed=req.speed,
            )
        except ValueError as ve:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
        except (TimeoutError, httpx.TimeoutException):
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="TTS provider request timed out",
            )
        except Exception as exc:
            err_msg = str(exc)
            if "429" in err_msg or "rate limit" in err_msg.lower():
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="TTS provider rate limit reached. Please retry in a few moments.",
                )
            log.warning("TTS generation failure: %s", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Speech synthesis upstream provider error",
            )

        # Store to persistent cache
        set_voice_cache(
            key=cache_key,
            audio_bytes=result.audio_bytes,
            metadata={
                "text": cleaned_text,
                "model": model,
                "voice": voice,
                "lang": lang,
                "style": style,
                "speed": req.speed,
                "duration_ms": result.duration_ms,
                "mime_type": result.mime_type,
                "timings": [
                    {"word": wt.word, "startMs": wt.start_ms, "endMs": wt.end_ms}
                    for wt in result.word_timings
                ],
            },
        )

        if format == "json":
            json_data = TTSResponseJSON(
                audio_base64=base64.b64encode(result.audio_bytes).decode("ascii"),
                mime_type=result.mime_type,
                duration_ms=result.duration_ms,
            )
            return Response(
                content=json_data.model_dump_json(),
                media_type="application/json",
                headers={"X-Cache": "MISS"},
            )

        return Response(
            content=result.audio_bytes,
            media_type=result.mime_type,
            headers={
                "Content-Disposition": "inline; filename=speech.wav",
                "Cache-Control": "public, max-age=86400",
                "X-Cache": "MISS",
            },
        )


async def _local_tts(req: TTSRequest, format: str) -> Response:
    """Free local pipeline: transliterate -> local female voice -> cache (with word timings)."""
    from app.voice.pipeline import synthesize_segment

    settings = get_settings()
    if not settings.voice_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Voice service is disabled")
    result = await synthesize_segment(req.text, voice=req.voice or "", speed=req.speed,
                                      lang=req.lang or "auto", provider=req.provider or None,
                                      gender=getattr(req, "gender", None) or "")
    if not result.audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Speech synthesis unavailable: no local TTS provider is installed or all failed",
        )
    ext = "mp3" if "mpeg" in result.mime_type else "wav"
    headers = {
        "X-Cache": "HIT" if result.from_cache else "MISS",
        "X-TTS-Provider": result.provider,
        "X-TTS-Lang": result.lang,
        "Cache-Control": "public, max-age=86400",
    }
    if format == "json":
        body = TTSResponseJSON(
            audio_base64=base64.b64encode(result.audio_bytes).decode("ascii"),
            mime_type=result.mime_type,
            duration_ms=result.duration_ms,
            word_timings=[t.to_dict() for t in result.word_timings],
            provider=result.provider,
            lang=result.lang,
            tts_text=result.tts_text,
            from_cache=result.from_cache,
        )
        return Response(content=body.model_dump_json(), media_type="application/json", headers=headers)
    headers["Content-Disposition"] = f"inline; filename=speech.{ext}"
    return Response(content=result.audio_bytes, media_type=result.mime_type, headers=headers)


# ---------------------------------------------------------------------------
# Voices catalogue + admin preview
# ---------------------------------------------------------------------------

@router.get("/voices", summary="List TTS providers/voices and which are installed")
async def list_voices() -> dict:
    from app.voice.tts.edge_tts_provider import ENGLISH_FEMALE, TAMIL_FEMALE, EdgeTTSProvider
    from app.voice.tts.indic_parler_provider import FEMALE_SPEAKERS, IndicParlerTTSProvider
    from app.voice.tts.kokoro_provider import FEMALE_VOICES as KOKORO_VOICES
    from app.voice.tts.kokoro_provider import KokoroTTSProvider, models_present
    from app.voice.tts.pocket_tts_provider import PocketTTSProvider

    s = get_settings()
    providers = [
        {"id": "pocket_tts", "label": "Pocket TTS Tamil (Geetha)", "license": "MIT", "local": True,
         "langs": ["tanglish"], "gender": "female", "voices": ["geetha"],
         "installed": PocketTTSProvider.is_installed()},
        {"id": "kokoro", "label": "Kokoro-82M", "license": "Apache-2.0", "local": True,
         "langs": ["english"], "gender": "female", "voices": list(KOKORO_VOICES),
         "installed": KokoroTTSProvider.is_installed(), "models_downloaded": models_present()},
        {"id": "edge", "label": "Microsoft Edge neural (online, free)", "license": "service",
         "local": False, "langs": ["tanglish", "english"], "gender": "female",
         "voices": [TAMIL_FEMALE, ENGLISH_FEMALE], "installed": EdgeTTSProvider.is_installed()},
        {"id": "indic_parler", "label": "AI4Bharat Indic Parler-TTS", "license": "Apache-2.0",
         "local": True, "langs": ["tanglish"], "gender": "female", "voices": list(FEMALE_SPEAKERS),
         "installed": IndicParlerTTSProvider.is_installed()},
        {"id": "bodhan", "label": "Bodhan Indic-Speak (paid)", "local": False, "langs": ["tanglish"],
         "voices": [s.bodhan_tts_voice], "installed": bool(s.bodhan_api_key)},
        {"id": "gemini", "label": "Gemini TTS (paid)", "local": False, "langs": ["tanglish", "english"],
         "voices": [s.bookey_tts_voice], "installed": bool(s.gemini_api_key)},
    ]
    return {
        "mode": s.tts_provider,
        "defaults": {
            "tanglish": {"provider": s.tts_tanglish_provider, "voice": s.tts_tanglish_voice,
                         "fallback": {"provider": "edge", "voice": s.edge_tts_voice}},
            "english": {"provider": s.tts_english_provider, "voice": s.tts_english_voice,
                        "fallback": {"provider": "edge", "voice": s.edge_tts_voice_en}},
        },
        "providers": providers,
    }


@router.post("/preview", summary="Admin: audition a provider/voice")
async def preview_voice(req: PreviewRequest, _admin=Depends(require_admin)) -> dict:
    import time as _time

    from app.voice.pipeline import synthesize_segment

    t0 = _time.monotonic()
    result = await synthesize_segment(req.text, voice=req.voice or "", speed=req.speed,
                                      lang=req.lang or "auto", provider=req.provider or None,
                                      gender=getattr(req, "gender", None) or "")
    compute_ms = int((_time.monotonic() - t0) * 1000)
    if not result.audio_bytes:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"Provider '{req.provider or 'default'}' unavailable")
    return {
        "audio_base64": base64.b64encode(result.audio_bytes).decode("ascii"),
        "mime_type": result.mime_type,
        "duration_ms": result.duration_ms,
        "compute_ms": compute_ms,
        "rtf": round(compute_ms / max(1, result.duration_ms), 3),
        "provider": result.provider,
        "voice": result.voice,
        "lang": result.lang,
        "tts_text": result.tts_text,
        "from_cache": result.from_cache,
        "word_timings": [t.to_dict() for t in result.word_timings],
    }


# ---------------------------------------------------------------------------
# STT: Speech-to-Text
# ---------------------------------------------------------------------------

@router.post("/stt", response_model=STTResponse, summary="Transcribe question audio recording")
@limiter.limit(voice_limit)
async def speech_to_text(
    request: Request,
    file: UploadFile = File(..., description="Recorded audio file"),
    language: str | None = Form(default=None, description="Optional BCP-47 language hint, e.g. 'ta' or 'en'"),
    user: User = Depends(get_current_user),
) -> STTResponse:
    """
    Transcribes audio using local faster-whisper (default) or Groq Whisper.
    """
    settings = get_settings()

    # Validate file extension / format
    filename = (file.filename or "").lower()
    content_type = (file.content_type or "").lower()

    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    is_valid_format = (
        ext in SUPPORTED_AUDIO_EXTENSIONS
        or any(sub in content_type for sub in ["audio/", "video/webm", "ogg", "wav", "mp4"])
    )
    if not is_valid_format:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported audio format. Supported: {', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS))}",
        )

    # Read with a hard cap (never buffer more than the limit + 1 chunk)
    max_bytes = min(MAX_STT_AUDIO_BYTES, settings.max_stt_upload_bytes)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Audio file exceeds {max_bytes // (1024 * 1024)}MB limit",
            )
        chunks.append(chunk)
    audio_bytes = b"".join(chunks)
    if not audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded audio file is empty",
        )

    if len(audio_bytes) > MAX_STT_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Audio file exceeds {MAX_STT_AUDIO_BYTES // (1024 * 1024)}MB limit",
        )

    mime = content_type or f"audio/{ext}" if ext else "audio/webm"
    provider_name = settings.stt_provider.lower()

    if provider_name == "local":
        stt_provider = FasterWhisperSTT(
            model_name=settings.faster_whisper_model,
        )
    elif provider_name == "groq":
        if not settings.groq_api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Speech-to-text service is not configured (missing Groq API key)",
            )
        stt_provider = GroqWhisperSTT(
            api_key=settings.groq_api_key,
            model=settings.bookey_stt_model or settings.groq_whisper_model,
            vocab_hint=DEFAULT_TANG_VOCAB_HINT,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"STT provider '{provider_name}' is not configured",
        )

    try:
        result = await stt_provider.transcribe(
            audio_bytes=audio_bytes,
            mime=mime,
            hint_lang=language,
        )
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except (TimeoutError, httpx.TimeoutException):
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="STT provider request timed out",
        )
    except Exception as exc:
        err_msg = str(exc)
        if "429" in err_msg or "rate limit" in err_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="STT provider rate limit reached. Please retry in a few moments.",
            )
        log.warning("STT transcription failure: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Speech-to-text upstream provider error",
        )

    return STTResponse(
        transcript=result.transcript,
        language=result.language,
        duration_s=result.duration_s,
    )


# ---------------------------------------------------------------------------
# Intent: classify an ambiguous lesson command (R1 voice commands)
# ---------------------------------------------------------------------------

VOICE_INTENTS = (
    "stop", "resume", "next_page", "previous_page", "repeat", "slower", "faster", "simpler",
    "run_code", "show_diagram", "quiz", "goto_chapter", "goto_page", "question",
)
INTENT_TIMEOUT_S = 4.0

_INTENT_SYSTEM = (
    "You route what a learner said to an AI teacher during a lesson. The learner speaks "
    "Tanglish (Tamil in Latin letters mixed with English), English, or Tamil script, and the "
    "text may contain speech-recognition mistakes. Decide whether it is a lesson CONTROL "
    "command or a question/statement for the teacher.\n"
    "Intents: stop (pause/wait/niruthu), resume (continue/sollunga/start), next_page, "
    "previous_page (go back/munnaadi page), repeat (once more/thirumba sollu; args.scope='page' "
    "to restart the page), slower, faster, simpler (explain again more simply/puriyala), run_code, "
    "show_diagram, quiz (quiz me/test pannu), goto_chapter (args.chapter, optional args.page, or "
    "args.chapterDelta 1/-1), goto_page (args.page), question (anything else, including any "
    "question about the subject).\n"
    "When unsure, answer 'question'. Reply ONLY with JSON: "
    '{"intent": "...", "args": {}, "confidence": 0.0-1.0}'
)

_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(VOICE_INTENTS)},
        "args": {
            "type": "object",
            "properties": {
                "chapter": {"type": "integer"},
                "page": {"type": "integer"},
                "chapterDelta": {"type": "integer"},
                "scope": {"type": "string", "enum": ["sentence", "page"]},
            },
        },
        "confidence": {"type": "number"},
    },
    "required": ["intent", "confidence"],
}


class IntentRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=300)
    lang: str | None = Field(default=None, description="'tanglish' | 'english'")
    page_title: str | None = Field(default=None, max_length=200)
    local_intent: str | None = Field(default=None, max_length=32)
    local_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class IntentResponse(BaseModel):
    intent: str
    args: dict = Field(default_factory=dict)
    confidence: float
    source: str = "llm"


def _clean_intent_args(intent: str, raw: object) -> dict | None:
    args = raw if isinstance(raw, dict) else {}
    out: dict = {}
    for key in ("chapter", "page"):
        value = args.get(key)
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 1000:
            out[key] = value
    if args.get("chapterDelta") in (1, -1):
        out["chapterDelta"] = args["chapterDelta"]
    if args.get("scope") in ("sentence", "page"):
        out["scope"] = args["scope"]
    if intent == "goto_chapter" and "chapter" not in out and "chapterDelta" not in out:
        return None
    if intent == "goto_page" and "page" not in out:
        return None
    return out


def _parse_intent_reply(text: str) -> IntentResponse | None:
    import json
    import re

    raw = (text or "").strip()
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    intent = data.get("intent")
    if intent not in VOICE_INTENTS:
        return None
    args = _clean_intent_args(intent, data.get("args"))
    if args is None:
        return None
    try:
        confidence = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return IntentResponse(intent=intent, args=args, confidence=round(confidence, 3))


@router.post("/intent", response_model=IntentResponse, summary="Classify a spoken/typed lesson command")
@limiter.limit(voice_limit)
async def classify_intent(
    request: Request,
    req: IntentRequest,
    user: User = Depends(get_current_user),
) -> IntentResponse:
    """
    Used by the reader only when its local matcher is unsure. Auth required; rate-limited
    like the other voice routes. One tiny JSON LLM call with a short timeout; any failure
    returns `question` (confidence 0) so the reader falls back to asking the tutor.
    """
    import asyncio

    from app.llm.router import get_llm_router

    text = " ".join(req.text.split())[:300]
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty text")
    lang = req.lang if req.lang in ("tanglish", "english") else "tanglish"
    lines = [f'Learner said: "{text}"', f"Learner language: {lang}"]
    if req.page_title:
        lines.append(f'Current page: "{" ".join(req.page_title.split())[:120]}"')
    if req.local_intent in VOICE_INTENTS:
        lines.append(f"Keyword matcher guessed: {req.local_intent} ({req.local_confidence or 0:.2f})")
    prompt = "\n".join(lines)

    fallback = IntentResponse(intent="question", args={}, confidence=0.0, source="fallback")
    try:
        resp = await asyncio.wait_for(
            get_llm_router().generate_json(
                prompt=prompt,
                schema=_INTENT_SCHEMA,
                system=_INTENT_SYSTEM,
                temperature=0.0,
                max_tokens=80,
                timeout=INTENT_TIMEOUT_S,
            ),
            timeout=INTENT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.info("voice intent: LLM timed out")
        return fallback
    except Exception as exc:  # LLMError (all providers exhausted) or provider bug
        log.warning("voice intent: LLM failed: %s", type(exc).__name__)
        return fallback
    parsed = _parse_intent_reply(resp.text)
    return parsed or fallback
