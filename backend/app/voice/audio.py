"""
app/voice/audio.py — Small PCM helpers shared by the local TTS providers.

Internal representation: mono float32 numpy array in [-1, 1] + sample rate.
numpy is imported lazily so the server boots without the voice extras.
ffmpeg (if on PATH) is used for MP3 encode/decode and pitch-preserving tempo.
"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_SR = 24000


@dataclass
class PCMAudio:
    samples: Any                 # np.ndarray float32 mono
    sample_rate: int = DEFAULT_SR
    # Optional engine word boundaries in TTS-word space: list[(word, start_ms, end_ms)]
    boundaries: list[tuple[str, int, int]] = field(default_factory=list)
    speed_applied: bool = False  # True if the engine already applied the speed factor

    @property
    def duration_ms(self) -> int:
        return int(len(self.samples) * 1000 / max(1, self.sample_rate))


def backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_backend_path(raw: str) -> Path:
    """Resolve a settings path relative to backend/ (accepts a 'backend/' prefix)."""
    p = Path(raw)
    if p.is_absolute():
        return p
    s = raw.replace("\\", "/")
    if s.startswith("backend/"):
        s = s[len("backend/"):]
    if s.startswith("./"):
        s = s[2:]
    return backend_root() / s


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def _np():
    import numpy as np
    return np


def to_mono_float(samples, ) :
    np = _np()
    a = np.asarray(samples, dtype=np.float32)
    if a.ndim > 1:
        a = a.reshape(a.shape[0], -1).mean(axis=1) if a.shape[0] > a.shape[-1] else a.mean(axis=0)
    return a.reshape(-1)


def silence(ms: int, sr: int = DEFAULT_SR):
    np = _np()
    return np.zeros(max(0, int(sr * ms / 1000)), dtype=np.float32)


def rms(samples) -> float:
    np = _np()
    a = np.asarray(samples, dtype=np.float32)
    return float(np.sqrt(np.mean(a ** 2))) if a.size else 0.0


def trim_silence(samples, sr: int = DEFAULT_SR, threshold_db: float = -42.0, pad_ms: int = 40):
    """Trim leading/trailing silence (frame RMS below threshold), keep a little padding."""
    a, _start = trim_silence_bounds(samples, sr, threshold_db, pad_ms)
    return a


def trim_silence_bounds(samples, sr: int = DEFAULT_SR, threshold_db: float = -42.0, pad_ms: int = 40):
    """Like trim_silence but also returns the number of samples removed at the start."""
    np = _np()
    a = np.asarray(samples, dtype=np.float32)
    if a.size == 0:
        return a, 0
    frame = max(1, int(sr * 0.01))
    n_frames = len(a) // frame
    if n_frames < 3:
        return a, 0
    frames = a[: n_frames * frame].reshape(n_frames, frame)
    energy = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    peak = float(energy.max())
    if peak <= 1e-6:
        return a, 0
    thresh = peak * (10 ** (threshold_db / 20))
    voiced = np.where(energy > thresh)[0]
    if voiced.size == 0:
        return a, 0
    pad = int(sr * pad_ms / 1000)
    start = max(0, voiced[0] * frame - pad)
    end = min(len(a), (voiced[-1] + 1) * frame + pad)
    return a[start:end], int(start)


def resample(samples, src_sr: int, dst_sr: int):
    np = _np()
    a = np.asarray(samples, dtype=np.float32)
    if src_sr == dst_sr or a.size == 0:
        return a
    n_out = int(round(len(a) * dst_sr / src_sr))
    x_old = np.linspace(0.0, 1.0, num=len(a), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, a).astype(np.float32)


def normalize_peak(samples, target: float = 0.89):
    np = _np()
    a = np.asarray(samples, dtype=np.float32)
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    if peak <= 1e-6:
        return a
    return (a * min(target / peak, 4.0)).astype(np.float32)


def fade(samples, sr: int = DEFAULT_SR, ms: int = 8):
    np = _np()
    a = np.array(samples, dtype=np.float32, copy=True)
    n = min(len(a) // 2, int(sr * ms / 1000))
    if n > 1:
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        a[:n] *= ramp
        a[-n:] *= ramp[::-1]
    return a


def time_stretch(samples, sr: int, speed: float):
    """Pitch-preserving tempo change via ffmpeg atempo; falls back to unchanged audio."""
    np = _np()
    if abs(speed - 1.0) < 0.02:
        return np.asarray(samples, dtype=np.float32)
    exe = ffmpeg_path()
    if not exe:
        log.debug("ffmpeg not found; speed %.2f not applied", speed)
        return np.asarray(samples, dtype=np.float32)
    speed = max(0.5, min(2.0, speed))
    raw = np.asarray(samples, dtype=np.float32).tobytes()
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1",
           "-i", "pipe:0", "-filter:a", f"atempo={speed:.3f}", "-f", "f32le", "-ar", str(sr),
           "-ac", "1", "pipe:1"]
    try:
        proc = subprocess.run(cmd, input=raw, capture_output=True, timeout=60, check=True)
        return np.frombuffer(proc.stdout, dtype=np.float32).copy()
    except Exception as exc:
        log.warning("ffmpeg atempo failed: %s", exc)
        return np.asarray(samples, dtype=np.float32)


def wav_bytes(samples, sr: int = DEFAULT_SR) -> bytes:
    np = _np()
    a = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm16 = (a * 32767.0).astype("<i2").tobytes()
    bio = io.BytesIO()
    with wave.open(bio, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16)
    return bio.getvalue()


def encode(samples, sr: int = DEFAULT_SR, fmt: str = "wav") -> tuple[bytes, str]:
    """Encode PCM to ('wav' | 'mp3') → (bytes, mime). MP3 needs ffmpeg; falls back to WAV."""
    fmt = (fmt or "wav").lower()
    if fmt == "mp3":
        exe = ffmpeg_path()
        if exe:
            np = _np()
            raw = np.clip(np.asarray(samples, dtype=np.float32), -1, 1).tobytes()
            cmd = [exe, "-hide_banner", "-loglevel", "error", "-f", "f32le", "-ar", str(sr),
                   "-ac", "1", "-i", "pipe:0", "-codec:a", "libmp3lame", "-b:a", "96k",
                   "-f", "mp3", "pipe:1"]
            try:
                proc = subprocess.run(cmd, input=raw, capture_output=True, timeout=60, check=True)
                if proc.stdout:
                    return proc.stdout, "audio/mpeg"
            except Exception as exc:
                log.warning("ffmpeg mp3 encode failed, using WAV: %s", exc)
    return wav_bytes(samples, sr), "audio/wav"


def decode(audio: bytes, mime: str = "", target_sr: int = DEFAULT_SR):
    """Decode WAV (stdlib) or anything else (ffmpeg) → (float32 mono, sr)."""
    np = _np()
    if not audio:
        return np.zeros(0, dtype=np.float32), target_sr
    if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(audio), "rb") as wf:
                sr = wf.getframerate()
                ch = wf.getnchannels()
                width = wf.getsampwidth()
                frames = wf.readframes(wf.getnframes())
            if width == 2:
                a = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
            elif width == 4:
                a = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
            else:
                a = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128) / 128.0
            if ch > 1:
                a = a.reshape(-1, ch).mean(axis=1)
            return resample(a, sr, target_sr), target_sr
        except Exception:
            pass
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError(f"Cannot decode {mime or 'audio'} without ffmpeg")
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-f", "f32le",
           "-ar", str(target_sr), "-ac", "1", "pipe:1"]
    proc = subprocess.run(cmd, input=audio, capture_output=True, timeout=60, check=True)
    return np.frombuffer(proc.stdout, dtype=np.float32).copy(), target_sr
