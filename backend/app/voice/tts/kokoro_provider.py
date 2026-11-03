"""
app/voice/tts/kokoro_provider.py — Local English female TTS (Kokoro-82M, Apache-2.0).

Backend: `kokoro-onnx` (ONNX Runtime, CPU). It bundles espeak-ng through
`espeakng-loader`, so no system espeak install is needed on Windows.
Voices: af_heart (default, warm US female), af_bella (US female), bf_emma (UK female).

Model files (kokoro-v1.0.onnx + voices-v1.0.bin, ~340 MB) live in
backend/.cache/models/kokoro/ and are downloaded on first use when
KOKORO_AUTO_DOWNLOAD=true (default).
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.voice import TextToSpeech, TTSResult
from app.voice.audio import PCMAudio, encode, resolve_backend_path

log = logging.getLogger(__name__)

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
FEMALE_VOICES = {
    "af_heart": "en-us", "af_bella": "en-us", "af_nicole": "en-us", "af_sarah": "en-us",
    "bf_emma": "en-gb", "bf_isabella": "en-gb",
}
MALE_VOICES = {"am_michael": "en-us", "am_adam": "en-us", "bm_george": "en-gb"}
VOICES = {**FEMALE_VOICES, **MALE_VOICES}

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kokoro")
_LOCK = threading.Lock()
_ENGINE = None


def _paths() -> tuple[Path, Path]:
    from app.config import get_settings
    s = get_settings()
    model = resolve_backend_path(getattr(s, "kokoro_model_path", ".cache/models/kokoro/kokoro-v1.0.onnx"))
    voices = resolve_backend_path(getattr(s, "kokoro_voices_path", ".cache/models/kokoro/voices-v1.0.bin"))
    return model, voices


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log.info("Downloading %s → %s", url, dest)
    urllib.request.urlretrieve(url, tmp)  # noqa: S310 — fixed GitHub release URL
    tmp.replace(dest)


def models_present() -> bool:
    m, v = _paths()
    return m.exists() and v.exists()


def _load():
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _LOCK:
        if _ENGINE is not None:
            return _ENGINE
        from app.config import get_settings
        from kokoro_onnx import Kokoro

        model, voices = _paths()
        if not (model.exists() and voices.exists()):
            if not getattr(get_settings(), "kokoro_auto_download", True):
                raise RuntimeError(f"Kokoro model files missing at {model.parent}")
            if not model.exists():
                _download(f"{RELEASE}/{model.name}", model)
            if not voices.exists():
                _download(f"{RELEASE}/voices-v1.0.bin", voices)
        t0 = time.monotonic()
        _ENGINE = Kokoro(str(model), str(voices))
        log.info("Kokoro loaded in %.1fs", time.monotonic() - t0)
        return _ENGINE


def _create_sync(text: str, voice: str, speed: float):
    engine = _load()
    lang = VOICES.get(voice, "en-us")
    samples, sr = engine.create(text, voice=voice, speed=max(0.5, min(2.0, speed)), lang=lang)
    return samples, sr


def warm_up() -> None:
    _EXECUTOR.submit(_create_sync, "Hello.", "af_heart", 1.0).result()


class KokoroTTSProvider(TextToSpeech):
    name = "kokoro"
    voices = tuple(VOICES)
    default_voice = "af_heart"
    langs = ("english",)

    def __init__(self, voice: str = "af_heart", output_format: str = "wav") -> None:
        self.default_voice = voice if voice in VOICES else "af_heart"
        self._fmt = output_format

    @classmethod
    def is_installed(cls) -> bool:
        return importlib.util.find_spec("kokoro_onnx") is not None

    async def synthesize_pcm(self, text: str, voice: str = "", speed: float = 1.0) -> PCMAudio:
        import numpy as np

        if not text.strip():
            raise ValueError("Cannot synthesize empty text")
        if not self.is_installed():
            raise RuntimeError("kokoro-onnx not installed: pip install kokoro-onnx")
        loop = asyncio.get_running_loop()
        samples, sr = await loop.run_in_executor(_EXECUTOR, _create_sync, text, self.pick_voice(voice), speed)
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            raise RuntimeError("Kokoro produced no audio")
        return PCMAudio(samples=samples, sample_rate=int(sr), speed_applied=True)

    async def synthesize(self, text: str, voice: str = "", speed: float = 1.0) -> TTSResult:
        pcm = await self.synthesize_pcm(text, voice, speed)
        audio, mime = await asyncio.to_thread(encode, pcm.samples, pcm.sample_rate, self._fmt)
        return TTSResult(audio_bytes=audio, mime_type=mime, duration_ms=pcm.duration_ms,
                         provider=self.name, voice=self.pick_voice(voice), sample_rate=pcm.sample_rate)
