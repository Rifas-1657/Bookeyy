"""
app/voice/tts/indic_parler_provider.py — ai4bharat/indic-parler-tts (Apache-2.0), OPTIONAL.

Description-controlled expressive Tamil female voice (e.g. "Jaya", "Kavitha").
Only used when TTS_PROVIDER=indic_parler AND an HF token is present (the model
is gated on Hugging Face). Heavy (~0.9B params, torch + parler-tts), so it is
not installed by default:

    pip install git+https://github.com/huggingface/parler-tts.git

Input should be Tamil script (see app.voice.transliterate).
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from app.voice import TextToSpeech, TTSResult
from app.voice.audio import PCMAudio, encode

log = logging.getLogger(__name__)

MODEL_ID = "ai4bharat/indic-parler-tts"
FEMALE_SPEAKERS = ("Jaya", "Kavitha")
DEFAULT_DESCRIPTION = (
    "{speaker} speaks in a warm, friendly and expressive teaching tone at a moderate pace, "
    "with clear pronunciation. The recording is very high quality with no background noise."
)

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="indic-parler")
_LOCK = threading.Lock()
_BUNDLE = None


def hf_token() -> str:
    try:
        from app.config import get_settings
        tok = getattr(get_settings(), "hf_token", "") or ""
    except Exception:
        tok = ""
    return tok or os.environ.get("HF_TOKEN", "") or os.environ.get("HUGGING_FACE_HUB_TOKEN", "")


def _load():
    global _BUNDLE
    if _BUNDLE is not None:
        return _BUNDLE
    with _LOCK:
        if _BUNDLE is not None:
            return _BUNDLE
        import torch
        from parler_tts import ParlerTTSForConditionalGeneration
        from transformers import AutoTokenizer

        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        tok = hf_token() or None
        model = ParlerTTSForConditionalGeneration.from_pretrained(MODEL_ID, token=tok).to(device)
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, token=tok)
        desc_tok = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path, token=tok)
        _BUNDLE = (model, tokenizer, desc_tok, device)
        return _BUNDLE


def _generate_sync(text: str, speaker: str, description: str):
    import numpy as np
    import torch

    model, tokenizer, desc_tok, device = _load()
    desc = (description or DEFAULT_DESCRIPTION).format(speaker=speaker)
    d = desc_tok(desc, return_tensors="pt").to(device)
    p = tokenizer(text, return_tensors="pt").to(device)
    with torch.inference_mode():
        gen = model.generate(input_ids=d.input_ids, attention_mask=d.attention_mask,
                             prompt_input_ids=p.input_ids, prompt_attention_mask=p.attention_mask)
    audio = gen.cpu().numpy().squeeze().astype(np.float32)
    return audio, int(model.config.sampling_rate)


class IndicParlerTTSProvider(TextToSpeech):
    name = "indic_parler"
    voices = FEMALE_SPEAKERS
    default_voice = "Jaya"
    langs = ("tanglish", "tamil")

    def __init__(self, voice: str = "Jaya", description: str = "", output_format: str = "wav") -> None:
        self.default_voice = voice if voice in FEMALE_SPEAKERS else "Jaya"
        self._description = description
        self._fmt = output_format

    @classmethod
    def is_installed(cls) -> bool:
        return importlib.util.find_spec("parler_tts") is not None and bool(hf_token())

    async def synthesize_pcm(self, text: str, voice: str = "", speed: float = 1.0) -> PCMAudio:
        if not self.is_installed():
            raise RuntimeError("indic-parler-tts unavailable (needs parler-tts package and HF token)")
        loop = asyncio.get_running_loop()
        samples, sr = await loop.run_in_executor(
            _EXECUTOR, _generate_sync, text, self.pick_voice(voice), self._description)
        return PCMAudio(samples=samples, sample_rate=sr, speed_applied=False)

    async def synthesize(self, text: str, voice: str = "", speed: float = 1.0) -> TTSResult:
        from app.voice.audio import time_stretch
        pcm = await self.synthesize_pcm(text, voice, speed)
        samples = await asyncio.to_thread(time_stretch, pcm.samples, pcm.sample_rate, speed)
        audio, mime = await asyncio.to_thread(encode, samples, pcm.sample_rate, self._fmt)
        return TTSResult(audio_bytes=audio, mime_type=mime,
                         duration_ms=int(len(samples) * 1000 / pcm.sample_rate),
                         provider=self.name, voice=self.pick_voice(voice), sample_rate=pcm.sample_rate)
