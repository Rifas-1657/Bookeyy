"""
tools/voice-space/app.py — Bookey's own voice service (a Hugging Face Space, free CPU).

  POST /tts  {"text", "lang": "tanglish"|"english", "gender": "female"|"male", "speed"}
             -> {"audio_base64", "mime", "duration_ms", "sample_rate"}
  POST /stt  multipart file + optional language, context -> {"transcript", "language", "duration_s"}
  GET  /health

Every call needs the X-Voice-Token header (VOICE_TOKEN secret). Models come from the private
model repo MODEL_REPO at startup (HF_TOKEN secret), so the Space code can be public while the
models and the API stay private.

Tanglish: Bookey's Piper voice (Tamil script in, two averaged "house" speakers out).
English:  the public CC-BY Piper LibriTTS-R voice. STT: fine-tuned whisper-small (CTranslate2 int8).
"""
from __future__ import annotations

import base64
import hmac
import io
import os
import tempfile
import wave
from functools import lru_cache

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from huggingface_hub import hf_hub_download, snapshot_download
from pydantic import BaseModel, Field

MODEL_REPO = os.environ.get("MODEL_REPO", "mohamedraj/bookey-voice-models")
TOKEN = os.environ.get("VOICE_TOKEN", "")
EN_SPEAKERS = {"female": int(os.environ.get("EN_FEMALE_ID", "0")), "male": int(os.environ.get("EN_MALE_ID", "1"))}
app = FastAPI(title="Bookey voice")


def _auth(token: str | None) -> None:
    if not TOKEN or not hmac.compare_digest(token or "", TOKEN):
        raise HTTPException(status_code=401, detail="bad token")


@lru_cache(maxsize=1)
def _models() -> str:
    return snapshot_download(MODEL_REPO, token=os.environ.get("HF_TOKEN"))


@lru_cache(maxsize=2)
def _voice(lang: str):
    from piper import PiperVoice
    if lang == "english":
        onnx = hf_hub_download("rhasspy/piper-voices", "en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx")
        hf_hub_download("rhasspy/piper-voices", "en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx.json")
        return PiperVoice.load(onnx)
    return PiperVoice.load(os.path.join(_models(), "tts", "ta-bookey-medium.onnx"))


@lru_cache(maxsize=1)
def _whisper():
    from faster_whisper import WhisperModel
    return WhisperModel(os.path.join(_models(), "stt"), device="cpu", compute_type="int8",
                        cpu_threads=os.cpu_count() or 2)


def _speaker(lang: str, gender: str, voice) -> int:
    if lang == "english":
        return EN_SPEAKERS.get(gender, EN_SPEAKERS["female"])
    ids = voice.config.speaker_id_map or {}
    return ids.get(f"bookey_{gender}", ids.get("bookey_female", 0))


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=1200)
    lang: str = "tanglish"
    gender: str = "female"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


def _synthesize(body: TTSIn) -> dict:
    from piper import SynthesisConfig
    lang = "english" if body.lang == "english" else "tanglish"
    voice = _voice(lang)
    config = SynthesisConfig(speaker_id=_speaker(lang, body.gender, voice), length_scale=1.0 / body.speed)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        voice.synthesize_wav(body.text, wav, syn_config=config)
        frames, rate = wav.getnframes(), wav.getframerate()
    return {"audio_base64": base64.b64encode(buf.getvalue()).decode(), "mime": "audio/wav",
            "duration_ms": int(frames * 1000 / rate), "sample_rate": rate}


@app.post("/tts")
async def tts(body: TTSIn, x_voice_token: str | None = Header(default=None)) -> dict:
    _auth(x_voice_token)
    return await run_in_threadpool(_synthesize, body)


def _transcribe(path: str, language: str | None, context: str | None) -> dict:
    segments, info = _whisper().transcribe(path, language=language or "ta", beam_size=1,
                                           initial_prompt=context or None, vad_filter=True)
    return {"transcript": " ".join(s.text.strip() for s in segments).strip(), "language": info.language,
            "duration_s": info.duration}


@app.post("/stt")
async def stt(file: UploadFile = File(...), language: str | None = Form(default=None),
              context: str | None = Form(default=None), x_voice_token: str | None = Header(default=None)) -> dict:
    _auth(x_voice_token)
    data = await file.read()
    if not data or len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="audio must be 1 byte to 10 MB")
    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(file.filename or "a.webm")[1]) as tmp:
        tmp.write(data)
        tmp.flush()
        return await run_in_threadpool(_transcribe, tmp.name, language, (context or "")[:400])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.on_event("startup")
async def _warm() -> None:
    # Load models now so the first learner doesn't wait for downloads.
    await run_in_threadpool(lambda: (_voice("tanglish"), _voice("english"), _whisper()))
