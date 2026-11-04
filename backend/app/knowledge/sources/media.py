"""
app/knowledge/sources/media.py — uploaded video / audio file → timed transcript document.

Pipeline (runs inside the durable course-build job, never in the request):
  1. ffmpeg → 16 kHz mono WAV                                  ("Extracting audio")
  2. local faster-whisper (shared model with voice STT; GPU when available, CPU fallback)
     with timestamps                                           ("Transcribing 12:30 / 45:00")
  3. transcript chunked by time into "## Part n (m:ss–m:ss)" sections (split on pauses)
  4. video only: a few key frames (scene changes on key frames; evenly spaced fallback),
     blank / near-duplicate frames dropped, then slides / diagrams in them read by the vision
     model (or tesseract OCR) and woven into the matching section ("Reading slides 2/6")

Env:
  FFMPEG_PATH                     ffmpeg binary (else PATH)
  MEDIA_WHISPER_MODEL             default "auto" (large-v3-turbo on CUDA, FASTER_WHISPER_CPU_MODEL on CPU)
  MEDIA_WHISPER_BEAM              default 5
  MEDIA_SECTION_SECONDS           default 300   target length of one transcript section
  MEDIA_MAX_FRAMES                default 8     key frames sent to the vision model (0 = off)
  MEDIA_SCENE_THRESHOLD           default 0.3
  MEDIA_FFMPEG_TIMEOUT_S          default 1800
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from app.knowledge.sources.base import SourceDocument, SourceError, TimedSegment, fmt_ts

log = logging.getLogger(__name__)

ProgressFn = Callable[[str], Awaitable[None]]

VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/webm", "video/x-matroska"}
AUDIO_MIMES = {"audio/mp4", "audio/mpeg", "audio/wav", "audio/ogg", "audio/aac", "audio/flac", "audio/webm"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Magic bytes
# ─────────────────────────────────────────────────────────────────────────────

_EXT_MIME = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".aac": "audio/aac", ".flac": "audio/flac",
}


def detect_media_mime(header: bytes, filename: str) -> str | None:
    """Container signature check (extension must agree with the bytes). None = not a media file."""
    ext = os.path.splitext(filename or "")[1].lower()
    h = header[:64]
    iso = h[4:8] == b"ftyp" or h[4:8] in (b"moov", b"mdat", b"wide", b"free", b"skip")
    ebml = h[:4] == b"\x1a\x45\xdf\xa3"
    if ext in (".mp4", ".m4v", ".mov", ".m4a") and iso:
        return _EXT_MIME[ext]
    if ext in (".webm", ".mkv") and ebml:
        return _EXT_MIME[ext]
    if ext == ".mp3" and (h[:3] == b"ID3" or (len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0)):
        return "audio/mpeg"
    if ext == ".wav" and h[:4] == b"RIFF" and h[8:12] == b"WAVE":
        return "audio/wav"
    if ext in (".ogg", ".opus") and h[:4] == b"OggS":
        return "audio/ogg"
    if ext == ".aac" and len(h) > 1 and h[0] == 0xFF and (h[1] & 0xF6) == 0xF0:
        return "audio/aac"
    if ext == ".flac" and h[:4] == b"fLaC":
        return "audio/flac"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# ffmpeg helpers
# ─────────────────────────────────────────────────────────────────────────────

def ffmpeg_path() -> str | None:
    env = os.getenv("FFMPEG_PATH", "").strip()
    if env and Path(env).exists():
        return env
    return shutil.which("ffmpeg")


def _need_ffmpeg() -> str:
    exe = ffmpeg_path()
    if not exe:
        raise SourceError("Video/audio processing needs ffmpeg, which is not installed on this server.")
    return exe


async def _run(args: list[str], timeout: float) -> tuple[int, bytes, bytes]:
    from app.knowledge.sources.youtube import run_cmd
    return await run_cmd(args, timeout=timeout)


@dataclass
class MediaInfo:
    duration: float | None
    has_video: bool
    has_audio: bool


_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def parse_ffmpeg_info(stderr: str) -> MediaInfo:
    m = _DUR_RE.search(stderr)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    streams = [l for l in stderr.splitlines() if "Stream #" in l]
    has_video = any(" Video:" in l and "attached pic" not in l and "(mjpeg)" not in l.lower() and " png" not in l
                    for l in streams)
    has_audio = any(" Audio:" in l for l in streams)
    return MediaInfo(duration=dur, has_video=has_video, has_audio=has_audio)


async def probe_media(path: str) -> MediaInfo:
    """`ffmpeg -i` (no ffprobe needed): duration + which streams exist."""
    exe = _need_ffmpeg()
    _code, _out, err = await _run([exe, "-hide_banner", "-nostdin", "-i", path], timeout=60)
    info = parse_ffmpeg_info(err.decode("utf-8", "replace"))
    if not info.has_audio and not info.has_video:
        raise SourceError("This file has no readable audio or video stream (is it corrupted?).")
    return info


async def extract_audio(src: str, dst_wav: str) -> float:
    """→ 16 kHz mono 16-bit WAV. Returns its duration in seconds."""
    exe = _need_ffmpeg()
    code, _out, err = await _run(
        [exe, "-hide_banner", "-nostdin", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", "-f", "wav", dst_wav],
        timeout=_env_int("MEDIA_FFMPEG_TIMEOUT_S", 1800),
    )
    if code != 0 or not Path(dst_wav).exists():
        tail = " / ".join(err.decode("utf-8", "replace").strip().splitlines()[-2:])
        raise SourceError(f"Could not extract audio from the file ({tail[:300]}).")
    with wave.open(dst_wav, "rb") as w:
        return w.getnframes() / float(w.getframerate() or 16000)


_PTS_RE = re.compile(r"pts_time:\s*([0-9.]+)")


async def extract_key_frames(src: str, out_dir: str, duration: float | None, max_frames: int) -> list[tuple[float, str]]:
    """Scene-change frames (key frames only, so it is fast), else evenly spaced ones."""
    if max_frames <= 0:
        return []
    exe = _need_ffmpeg()
    thr = _env_float("MEDIA_SCENE_THRESHOLD", 0.3)
    pattern = str(Path(out_dir) / "scene_%03d.jpg")
    code, _out, err = await _run(
        [exe, "-hide_banner", "-nostdin", "-y", "-skip_frame", "nokey", "-i", src, "-an",
         "-vf", f"select='eq(n,0)+gt(scene,{thr})',showinfo,scale='min(1280,iw)':-2",
         "-fps_mode", "vfr", "-frames:v", str(max_frames * 4), "-q:v", "3", pattern],
        timeout=_env_int("MEDIA_FFMPEG_TIMEOUT_S", 1800),
    )
    times = [float(t) for t in _PTS_RE.findall(err.decode("utf-8", "replace"))]
    files = sorted(Path(out_dir).glob("scene_*.jpg"))
    frames = [(times[i] if i < len(times) else 0.0, str(f)) for i, f in enumerate(files)]
    if len(frames) >= 2 or not duration:
        return frames
    # static or single-shot video: sample evenly
    n = max(1, min(max_frames, int(duration // 60) + 2))
    out: list[tuple[float, str]] = []
    for k in range(n):
        t = duration * (k + 0.5) / n
        dst = str(Path(out_dir) / f"even_{k:03d}.jpg")
        c, _o, _e = await _run([exe, "-hide_banner", "-nostdin", "-y", "-ss", f"{t:.2f}", "-i", src,
                                "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-q:v", "3", dst], timeout=120)
        if c == 0 and Path(dst).exists():
            out.append((t, dst))
    return out or frames


def pick_frames(frames: list[tuple[float, bytes]], max_frames: int) -> list[tuple[float, bytes]]:
    """Drop blank and near-duplicate frames, then keep at most `max_frames` spread over time."""
    from app.knowledge.sources.images import frame_signature, thumb_distance
    kept: list[tuple[float, bytes]] = []
    last_thumb: list[int] | None = None
    for t, data in frames:
        try:
            std, thumb = frame_signature(data)
        except Exception:
            continue
        if std < 6.0:                                     # solid colour / black screen
            continue
        if last_thumb is not None and thumb_distance(thumb, last_thumb) < 6.0:
            continue
        kept.append((t, data))
        last_thumb = thumb
    if len(kept) > max_frames:
        step = len(kept) / max_frames
        kept = [kept[int(i * step)] for i in range(max_frames)]
    return kept


# ─────────────────────────────────────────────────────────────────────────────
# Transcription
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Transcript:
    segments: list[TimedSegment]
    language: str | None = None
    device: str = ""
    model: str = ""


def transcribe_file(wav_path: str, state: dict | None = None) -> Transcript:
    """Blocking faster-whisper transcription (call via asyncio.to_thread).
    `state["pos"]` is updated with the end time of the latest segment (progress)."""
    from app.voice.stt.faster_whisper import ALLOWED_LANGS, _pick_lang, get_whisper_model, resolve_model_config
    name = os.getenv("MEDIA_WHISPER_MODEL", "auto")
    model = get_whisper_model(name)
    if model is None:
        from app.config import get_settings
        st = get_settings()
        if st.stt_provider.lower() == "groq" and st.groq_api_key:
            return transcribe_file_groq(wav_path, st.groq_api_key,
                                        st.bookey_stt_model or st.groq_whisper_model, state)
        raise SourceError("Local transcription (faster-whisper) is not installed on this server.")
    kwargs = dict(task="transcribe", beam_size=max(1, _env_int("MEDIA_WHISPER_BEAM", 5)), vad_filter=True,
                  condition_on_previous_text=False)
    segs, info = model.transcribe(wav_path, **kwargs)
    lang = getattr(info, "language", None)
    prob = getattr(info, "language_probability", 1.0) or 0.0
    if isinstance(lang, str) and lang not in ALLOWED_LANGS and isinstance(prob, (int, float)) and prob < 0.6:
        # unsure guess (often hi/ml/te for Tanglish speech) → best of ta / en
        lang = _pick_lang(info) or "en"
        segs, info = model.transcribe(wav_path, language=lang, **kwargs)
    out: list[TimedSegment] = []
    for s in segs:
        txt = (s.text or "").strip()
        if txt:
            out.append(TimedSegment(start=float(s.start), end=float(s.end), text=txt))
        if state is not None:
            state["pos"] = float(s.end)
    device = str(getattr(getattr(model, "model", None), "device", "") or "")
    if not device:
        try:
            device = resolve_model_config(name)[1]
        except Exception:
            device = ""
    return Transcript(segments=out, language=lang if isinstance(lang, str) else None, device=device, model=name)


def transcribe_file_groq(wav_path: str, api_key: str, model: str, state: dict | None = None,
                         chunk_seconds: int | None = None) -> Transcript:
    """Blocking Groq Whisper transcription for hosts without faster-whisper (production).
    The WAV is cut into ~10-minute MP3 chunks (Groq's free tier takes <= 25 MB per file)."""
    import httpx
    exe = _need_ffmpeg()
    chunk = max(60, chunk_seconds or _env_int("MEDIA_GROQ_CHUNK_S", 600))
    with wave.open(wav_path, "rb") as w:
        total = w.getnframes() / float(w.getframerate() or 16000)
    out: list[TimedSegment] = []
    lang: str | None = None
    with tempfile.TemporaryDirectory(prefix="bookey_groq_") as tmp, httpx.Client(timeout=180.0) as client:
        start = 0.0
        k = 0
        while start < total:
            dst = str(Path(tmp) / f"c{k:03d}.mp3")
            import subprocess
            subprocess.run([exe, "-hide_banner", "-nostdin", "-y", "-ss", f"{start:.2f}", "-t", str(chunk),
                            "-i", wav_path, "-ac", "1", "-b:a", "48k", dst],
                           check=True, capture_output=True, timeout=600)
            for attempt in range(3):
                with open(dst, "rb") as fh:
                    r = client.post("https://api.groq.com/openai/v1/audio/transcriptions",
                                    headers={"Authorization": f"Bearer {api_key}"},
                                    files={"file": (f"c{k}.mp3", fh, "audio/mpeg")},
                                    data={"model": model, "response_format": "verbose_json"})
                if r.status_code == 429 and attempt < 2:
                    time.sleep(20 * (attempt + 1))
                    continue
                break
            if r.status_code >= 300:
                raise SourceError(f"Cloud transcription failed ({r.status_code}).")
            data = r.json()
            lang = lang or data.get("language")
            for seg in data.get("segments") or []:
                txt = (seg.get("text") or "").strip()
                if txt:
                    out.append(TimedSegment(start=start + float(seg.get("start", 0.0)),
                                            end=start + float(seg.get("end", 0.0)), text=txt))
            start += chunk
            k += 1
            if state is not None:
                state["pos"] = min(start, total)
    if isinstance(lang, str) and len(lang) > 2:   # Groq reports names ("english") → codes
        lang = {"english": "en", "tamil": "ta"}.get(lang.lower(), lang)
    return Transcript(segments=out, language=lang, device="groq", model=model)


def sections_from_segments(segments: list[TimedSegment], target_seconds: float) -> list[list[TimedSegment]]:
    """Group segments into ~target-long sections, preferring to cut at a pause (>1.2 s gap)
    once a section is 70 % full; hard cut at 130 %."""
    target = max(30.0, target_seconds)
    out: list[list[TimedSegment]] = []
    cur: list[TimedSegment] = []
    for i, s in enumerate(segments):
        if cur:
            length = (cur[-1].end or cur[-1].start) - cur[0].start
            gap = s.start - (cur[-1].end or cur[-1].start)
            if length >= target * 1.3 or (length >= target * 0.7 and gap > 1.2) or (length >= target and s.text[:1].isupper()):
                out.append(cur)
                cur = []
        cur.append(s)
    if cur:
        # a tiny tail joins the previous section
        tail_len = (cur[-1].end or cur[-1].start) - cur[0].start
        if out and tail_len < target * 0.25:
            out[-1].extend(cur)
        else:
            out.append(cur)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Document
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FrameNote:
    time: float
    kind: str
    text: str
    diagrams: list[str] = field(default_factory=list)


def build_media_document(
    *, title: str, filename: str, kind: str, duration: float | None, transcript: Transcript,
    frames: list[FrameNote], section_seconds: float, warnings: list[str], method: str,
) -> SourceDocument:
    label = "video" if kind == "video" else "audio recording"
    lines = [f"# {title}", "", f"Source: uploaded {label} `{filename}`"]
    if duration:
        lines.append(f"Duration: {fmt_ts(duration)}")
    if transcript.language:
        lines.append(f"Spoken language: {transcript.language}")
    sections = sections_from_segments(transcript.segments, section_seconds)
    pending = sorted(frames, key=lambda f: f.time)
    diagrams: list[str] = []
    for n, sec in enumerate(sections, start=1):
        start = sec[0].start
        end = sec[-1].end or sec[-1].start
        is_last = n == len(sections)
        lines += ["", f"## Part {n} ({fmt_ts(start)}–{fmt_ts(end)})", ""]
        para: list[str] = []
        for s in sec:
            para.append(f"[{fmt_ts(s.start)}] {s.text}")
        lines.append("\n".join(para))
        here = [f for f in pending if f.time < end or is_last]
        pending = [f for f in pending if f not in here]
        for f in here:
            lines += ["", f"> **On screen at {fmt_ts(f.time)}**" + (f" ({f.kind})" if f.kind else "") + ":"]
            if f.text:
                lines += ["> " + l for l in f.text.splitlines() if l.strip()]
            for d in f.diagrams:
                lines.append(f"> Diagram: {d}")
                diagrams.append(d)
    if not sections and frames:   # silent video with slides
        lines += ["", "## On-screen content", ""]
        for f in frames:
            lines += [f"### At {fmt_ts(f.time)}", f.text] + [f"- Diagram: {d}" for d in f.diagrams]
            diagrams += f.diagrams
    return SourceDocument(
        title=title, url="", kind="media", text="\n".join(lines).strip() + "\n",
        metadata={"durationSeconds": duration, "method": method, "warnings": warnings,
                  "segmentCount": len(transcript.segments), "sectionCount": len(sections),
                  "language": transcript.language, "frameCount": len(frames), "diagrams": diagrams[:40],
                  "mediaKind": kind},
        timestamps=transcript.segments,
    )


async def _noop(_: str) -> None:
    return None


async def media_to_document(
    path: str, *, title: str, filename: str, kind: str = "video", max_seconds: int | None = None,
    on_progress: ProgressFn | None = None,
) -> SourceDocument:
    """Full pipeline for one local media file. Raises SourceError with a learner-facing message."""
    progress = on_progress or _noop
    warnings: list[str] = []
    info = await probe_media(path)
    if max_seconds and info.duration and info.duration > max_seconds + 5:
        raise SourceError(f"This recording is {fmt_ts(info.duration)} long — the limit is {fmt_ts(max_seconds)}.")
    is_video = kind == "video" and info.has_video
    with tempfile.TemporaryDirectory(prefix="bookey_media_") as tmp:
        transcript = Transcript(segments=[])
        duration = info.duration
        if info.has_audio:
            await progress("Extracting audio")
            wav = str(Path(tmp) / "audio.wav")
            duration = await extract_audio(path, wav) or duration
            if max_seconds and duration and duration > max_seconds + 5:
                raise SourceError(f"This recording is {fmt_ts(duration)} long — the limit is {fmt_ts(max_seconds)}.")
            total = fmt_ts(duration)
            await progress(f"Transcribing 0:00 / {total}")
            state: dict = {"pos": 0.0}
            task = asyncio.create_task(asyncio.to_thread(transcribe_file, wav, state))
            last = -1.0
            t0 = time.monotonic()
            while not task.done():
                await asyncio.wait({task}, timeout=3.0)
                if state["pos"] != last and not task.done():
                    last = state["pos"]
                    await progress(f"Transcribing {fmt_ts(last)} / {total}")
            transcript = task.result()
            log.info("transcribed %s (%.0fs audio) in %.1fs on %s", filename, duration or 0,
                     time.monotonic() - t0, transcript.device)
        else:
            warnings.append("The file has no audio track — only on-screen content was used.")

        frames: list[FrameNote] = []
        max_frames = _env_int("MEDIA_MAX_FRAMES", 8)
        if is_video and max_frames > 0:
            await progress("Finding key frames")
            try:
                raw = await extract_key_frames(path, tmp, duration, max_frames)
                loaded = [(t, Path(p).read_bytes()) for t, p in raw]
                chosen = await asyncio.to_thread(pick_frames, loaded, max_frames)
            except SourceError as exc:
                warnings.append(f"Key frames skipped: {exc}")
                chosen = []
            from app.knowledge.sources.images import prepare_image, read_image
            unread: list[str] = []
            for i, (t, data) in enumerate(chosen, start=1):
                await progress(f"Reading slides {i}/{len(chosen)}")
                try:
                    jpeg, _w, _h = await asyncio.to_thread(prepare_image, data)
                except SourceError:
                    continue
                r = await read_image(jpeg, "frame")
                if r.error:
                    unread.append(r.error)
                    continue
                if r.kind == "other" or (r.method == "tesseract" and len(r.text.strip()) < 20 and not r.diagrams):
                    continue
                if not r.empty:
                    frames.append(FrameNote(time=t, kind=r.kind if r.kind not in ("other", "photo") else "",
                                            text=r.text, diagrams=r.diagrams))
            if unread and not frames:
                warnings.append(f"Key frames were not described: {unread[0]}"[:300])

    if not transcript.segments and not frames:
        raise SourceError("No speech or on-screen text was found in this recording.")
    method = ("Groq Whisper" if transcript.device == "groq"
              else "faster-whisper" + (f" ({transcript.device})" if transcript.device else ""))
    if frames:
        method += " + key frames"
    return build_media_document(
        title=title, filename=filename, kind="video" if is_video else "audio", duration=duration,
        transcript=transcript, frames=frames, section_seconds=_env_int("MEDIA_SECTION_SECONDS", 300),
        warnings=warnings, method=method,
    )
