"""
app/voice/stt/faster_whisper.py — Local faster-whisper STT (free, offline), tuned for Tanglish.

Model selection (FASTER_WHISPER_MODEL=auto, default):
  CUDA available → large-v3-turbo, int8_float16 on GPU (RTX 3050 4 GB: ~1 GB VRAM)
  otherwise      → FASTER_WHISPER_CPU_MODEL (default "small"), int8 on CPU
If the GPU path fails at load or first use (missing cuBLAS/cuDNN DLLs …) it
falls back to CPU automatically.

- VAD filter on; initial_prompt with Tanglish + tech vocabulary.
- Language: hint if given; otherwise auto-detect restricted to ta / en.
- Output is ALWAYS Latin script: Tamil-script output is romanized to Tanglish.
- Singleton model, non-blocking via asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

from app.voice import SpeechToText, STTResult

log = logging.getLogger(__name__)

DEFAULT_LOCAL_VOCAB_PROMPT = (
    "Vanakkam! Innaiku namma Python variables pathi kathukkalaam. React-la useState hook use "
    "pannumbodhu component re-render aagum. FastAPI, LangGraph, RAG, embeddings, vector database, "
    "API, StateGraph, function, loop, list, dict, class, print, code, memory, eppadi, enna, "
    "puriyala, sollunga, explain pannunga."
)

ALLOWED_LANGS = ("ta", "en")

_MODEL_CACHE: dict[str, object] = {}
_MODEL_LOCK = threading.Lock()
_DLL_DIRS_ADDED = False


def _add_nvidia_dll_dirs() -> None:
    """On Windows, expose pip-installed nvidia-cublas/cudnn DLLs to ctranslate2."""
    global _DLL_DIRS_ADDED
    if _DLL_DIRS_ADDED or sys.platform != "win32":
        return
    _DLL_DIRS_ADDED = True
    try:
        import nvidia  # namespace package from nvidia-*-cu12 wheels
        for base in getattr(nvidia, "__path__", []):
            for sub in Path(base).glob("*/bin"):
                try:
                    os.add_dll_directory(str(sub))
                    os.environ["PATH"] = str(sub) + os.pathsep + os.environ.get("PATH", "")
                except OSError:
                    pass
    except Exception:
        pass


def _cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def resolve_model_config(model_name: str = "auto") -> tuple[str, str, str]:
    """→ (model, device, compute_type) honoring settings."""
    try:
        from app.config import get_settings
        s = get_settings()
        device_pref = (s.faster_whisper_device or "auto").lower()
        compute_pref = (s.faster_whisper_compute_type or "auto").lower()
        cpu_model = s.faster_whisper_cpu_model or "small"
    except Exception:
        device_pref, compute_pref, cpu_model = "auto", "auto", "small"
    use_cuda = device_pref == "cuda" or (device_pref == "auto" and _cuda_available())
    if use_cuda:
        model = "large-v3-turbo" if model_name in ("", "auto") else model_name
        return model, "cuda", ("int8_float16" if compute_pref == "auto" else compute_pref)
    model = cpu_model if model_name in ("", "auto") else model_name
    return model, "cpu", ("int8" if compute_pref == "auto" else compute_pref)


def _load(model: str, device: str, compute: str):
    from faster_whisper import WhisperModel
    if device == "cuda":
        _add_nvidia_dll_dirs()
    log.info("Loading faster-whisper '%s' (device=%s, compute_type=%s)...", model, device, compute)
    m = WhisperModel(model, device=device, compute_type=compute)
    if device == "cuda":
        # Validate the CUDA path end-to-end (cuBLAS / cuDNN are only loaded on first use)
        import numpy as np
        segs, _ = m.transcribe(np.zeros(8000, dtype=np.float32), language="en", vad_filter=False)
        list(segs)
    return m


def get_whisper_model(model_name: str = "auto"):
    """Thread-safe singleton getter for the Faster-Whisper model (GPU → CPU fallback)."""
    if model_name not in _MODEL_CACHE:
        with _MODEL_LOCK:
            if model_name not in _MODEL_CACHE:
                try:
                    import faster_whisper  # noqa: F401
                except ImportError:
                    log.error("faster_whisper package is not installed")
                    _MODEL_CACHE[model_name] = None
                    return None
                model, device, compute = resolve_model_config(model_name)
                try:
                    _MODEL_CACHE[model_name] = _load(model, device, compute)
                except Exception as exc:
                    if device != "cuda":
                        log.error("Failed to load faster-whisper model '%s': %s", model, exc)
                        raise
                    log.warning("faster-whisper CUDA load failed (%s); falling back to CPU", exc)
                    cpu_model = model if model_name not in ("", "auto") else resolve_cpu_model()
                    _MODEL_CACHE[model_name] = _load(cpu_model, "cpu", "int8")
                log.info("faster-whisper ready (%s)", model_name)
    return _MODEL_CACHE[model_name]


def resolve_cpu_model() -> str:
    try:
        from app.config import get_settings
        return get_settings().faster_whisper_cpu_model or "small"
    except Exception:
        return "small"


def _pick_lang(info) -> str | None:
    """Best of ta/en from detection probabilities (None if unavailable)."""
    probs = getattr(info, "all_language_probs", None)
    if isinstance(probs, (list, tuple)) and probs:
        best = None
        for item in probs:
            try:
                code, p = item
            except (TypeError, ValueError):
                continue
            if code in ALLOWED_LANGS and (best is None or p > best[1]):
                best = (code, p)
        if best:
            return best[0]
    return None


class FasterWhisperSTT(SpeechToText):
    name = "faster_whisper"

    def __init__(
        self,
        model_name: str = "auto",
        vocab_hint: str = DEFAULT_LOCAL_VOCAB_PROMPT,
    ) -> None:
        self._model_name = model_name
        self._vocab_hint = vocab_hint

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime: str = "audio/webm",
        hint_lang: Optional[str] = None,
    ) -> STTResult:
        """Transcribe (never translate). Returns Latin-script Tanglish/English text."""
        if not audio_bytes:
            raise ValueError("Uploaded audio file is empty")

        if len(audio_bytes) > self.MAX_BYTES:
            raise ValueError(f"Audio file exceeds {self.MAX_BYTES // (1024 * 1024)}MB limit")

        mime_lower = (mime or "").lower()
        suffix = ".webm"
        if "wav" in mime_lower:
            suffix = ".wav"
        elif "ogg" in mime_lower:
            suffix = ".ogg"
        elif "mp4" in mime_lower or "m4a" in mime_lower:
            suffix = ".mp4"
        elif "mp3" in mime_lower or "mpeg" in mime_lower:
            suffix = ".mp3"

        hint = (hint_lang or "").lower() or None
        if hint in ("tanglish", "tamil"):
            hint = "ta"
        elif hint == "english":
            hint = "en"

        def _sync_transcribe() -> STTResult:
            from app.voice.transliterate import romanize_tamil

            model = get_whisper_model(self._model_name)
            if model is None:
                raise RuntimeError("Local faster_whisper STT engine is not available")

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
                tmp_file.write(audio_bytes)
                tmp_path = Path(tmp_file.name)

            try:
                kwargs = dict(task="transcribe", initial_prompt=self._vocab_hint,
                              beam_size=5, vad_filter=True)
                segments, info = model.transcribe(str(tmp_path), language=hint, **kwargs)
                detected = getattr(info, "language", None)
                if hint is None and isinstance(detected, str) and detected not in ALLOWED_LANGS:
                    # Whisper sometimes guesses hi/ml/te for Tanglish speech: re-run as ta/en
                    retry_lang = _pick_lang(info) or "ta"
                    segments, info = model.transcribe(str(tmp_path), language=retry_lang, **kwargs)
                text_parts = [segment.text.strip() for segment in segments if segment.text.strip()]
                full_text = romanize_tamil(" ".join(text_parts).strip())
                lang = getattr(info, "language", None)
                return STTResult(
                    transcript=full_text,
                    language=lang if isinstance(lang, str) and lang else (hint or "ta"),
                    confidence=float(getattr(info, "language_probability", 1.0) or 1.0)
                    if isinstance(getattr(info, "language_probability", 1.0), (int, float)) else 1.0,
                    duration_s=getattr(info, "duration", 0.0) if isinstance(getattr(info, "duration", 0.0), (int, float)) else 0.0,
                )
            finally:
                tmp_path.unlink(missing_ok=True)

        return await asyncio.to_thread(_sync_transcribe)
