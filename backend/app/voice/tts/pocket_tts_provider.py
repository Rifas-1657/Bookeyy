"""
app/voice/tts/pocket_tts_provider.py — Local Tamil female TTS (Pocket TTS "Geetha").

Model: prasadvittaldev/pocket-tts-tamil-female-audiobook (MIT), CPU int8, 24 kHz.
Input MUST be Tamil script (see app.voice.transliterate) for correct pronunciation.

The model is loaded once per process (singleton) and all generation runs on a
dedicated single worker thread, so the event loop is never blocked and the
(non-thread-safe) model is never used concurrently.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.voice import TextToSpeech, TTSResult
from app.voice.audio import PCMAudio, encode, resolve_backend_path, to_mono_float

log = logging.getLogger(__name__)

DEFAULT_MODEL_ID = "prasadvittaldev/pocket-tts-tamil-female-audiobook"

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pocket-tts")
_LOCK = threading.Lock()
_MODEL = None
_STATES: dict[str, object] = {}
_SR = 24000


def _settings():
    from app.config import get_settings
    return get_settings()


def _load_model():
    """Load (once) and return the Pocket TTS model. Runs on the worker thread."""
    global _MODEL, _SR
    if _MODEL is not None:
        return _MODEL
    with _LOCK:
        if _MODEL is not None:
            return _MODEL
        from huggingface_hub import hf_hub_download
        from pocket_tts import TTSModel

        s = _settings()
        model_id = getattr(s, "pocket_tts_model_id", DEFAULT_MODEL_ID) or DEFAULT_MODEL_ID
        t0 = time.monotonic()
        config_path = hf_hub_download(model_id, "config.yaml")
        model = TTSModel.load_model(config=config_path, quantize=bool(getattr(s, "pocket_tts_quantize", True)))
        try:
            model.to("cpu")
        except Exception:
            pass
        _SR = int(getattr(model, "sample_rate", 24000) or 24000)
        _MODEL = model
        log.info("Pocket TTS loaded (%s) in %.1fs, sr=%d", model_id, time.monotonic() - t0, _SR)
        return _MODEL


def _voice_state(voice: str):
    """Speaker-prompt state per voice (default: the repo's bundled Geetha prompt)."""
    key = voice or "geetha"
    if key in _STATES:
        return _STATES[key]
    model = _load_model()
    s = _settings()
    custom = getattr(s, "pocket_tts_voice_prompt", "") or ""
    if custom and key in ("geetha", "default"):
        prompt_path = str(resolve_backend_path(custom))
    elif key not in ("geetha", "default") and key.lower().endswith(".wav"):
        prompt_path = str(resolve_backend_path(key))
    else:
        from huggingface_hub import hf_hub_download
        model_id = getattr(s, "pocket_tts_model_id", DEFAULT_MODEL_ID) or DEFAULT_MODEL_ID
        prompt_path = hf_hub_download(model_id, "voice_prompt.wav")
    _STATES[key] = model.get_state_for_audio_prompt(prompt_path)
    return _STATES[key]


def _generate_sync(text: str, voice: str):
    import numpy as np

    model = _load_model()
    state = _voice_state(voice)
    chunks = []
    for c in model.generate_audio_stream(model_state=state, text_to_generate=text):
        try:
            c = c.detach().cpu().numpy()
        except AttributeError:
            pass
        chunks.append(to_mono_float(c))
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    return audio.astype(np.float32), _SR


def warm_up() -> None:
    """Load model + default voice in the worker thread (blocking)."""
    _EXECUTOR.submit(lambda: _voice_state("geetha")).result()


class PocketTTSProvider(TextToSpeech):
    name = "pocket_tts"
    voices = ("geetha",)
    default_voice = "geetha"
    langs = ("tanglish", "tamil")

    def __init__(self, voice: str = "geetha", output_format: str = "wav") -> None:
        self.default_voice = voice or "geetha"
        self._fmt = output_format

    @classmethod
    def is_installed(cls) -> bool:
        return importlib.util.find_spec("pocket_tts") is not None and \
            importlib.util.find_spec("huggingface_hub") is not None

    def pick_voice(self, voice: str = "") -> str:
        if voice and (voice in self.voices or voice.lower().endswith(".wav")):
            return voice
        return self.default_voice

    async def synthesize_pcm(self, text: str, voice: str = "", speed: float = 1.0) -> PCMAudio:
        if not text.strip():
            raise ValueError("Cannot synthesize empty text")
        if not self.is_installed():
            raise RuntimeError("pocket-tts not installed: pip install pocket-tts")
        loop = asyncio.get_running_loop()
        samples, sr = await loop.run_in_executor(_EXECUTOR, _generate_sync, text, self.pick_voice(voice))
        if samples.size == 0:
            raise RuntimeError("Pocket TTS produced no audio")
        return PCMAudio(samples=samples, sample_rate=sr, speed_applied=False)

    async def synthesize(self, text: str, voice: str = "", speed: float = 1.0) -> TTSResult:
        from app.voice.audio import time_stretch
        pcm = await self.synthesize_pcm(text, voice, speed)
        samples = await asyncio.to_thread(time_stretch, pcm.samples, pcm.sample_rate, speed)
        audio, mime = await asyncio.to_thread(encode, samples, pcm.sample_rate, self._fmt)
        return TTSResult(audio_bytes=audio, mime_type=mime,
                         duration_ms=int(len(samples) * 1000 / pcm.sample_rate),
                         provider=self.name, voice=self.pick_voice(voice), sample_rate=pcm.sample_rate)
