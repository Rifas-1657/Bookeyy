"""
app/knowledge/sources/youtube.py — YouTube video → transcript document.

Strategy (first that succeeds wins):
  1. `npx -y mcp-video-analyzer@0.11.0 analyze <url> --detail standard
      --fields metadata,transcript,chapters,timeline --max-frames N`
     (MIT tool; stdout = one JSON document, progress on stderr, exit 1 on failure).
     Requires node/npx and yt-dlp on PATH (the venv Scripts dir is prepended).
  2. yt-dlp captions directly (--write-subs --write-auto-subs, VTT → text).
  3. yt-dlp audio download + faster-whisper (lazy import; optional).

Playlists are rejected; `list=` on a watch URL is stripped.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.knowledge.sources.base import (
    SourceDocument,
    SourceError,
    SourceImage,
    SourcePreview,
    TimedSegment,
    clip,
    fmt_ts,
    http_client,
    parse_ts,
)

log = logging.getLogger(__name__)

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# ─────────────────────────────────────────────────────────────────────────────
# URL handling
# ─────────────────────────────────────────────────────────────────────────────

def is_youtube_url(url: str) -> bool:
    try:
        return (urlparse(url).hostname or "").lower() in _YT_HOSTS
    except Exception:
        return False


def normalize_youtube_url(url: str) -> tuple[str, str]:
    """Return (canonical_watch_url, video_id). Raises SourceError for playlists / bad URLs."""
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    if host not in _YT_HOSTS:
        raise SourceError("Not a YouTube URL.")
    if p.scheme not in ("http", "https"):
        raise SourceError("YouTube URL must use https.")
    qs = parse_qs(p.query)
    vid: str | None = None
    path = p.path.rstrip("/")
    if host.endswith("youtu.be"):
        vid = path.lstrip("/").split("/")[0] or None
    elif path == "/watch":
        vid = (qs.get("v") or [None])[0]
    elif path.startswith(("/shorts/", "/live/", "/embed/", "/v/")):
        vid = path.split("/")[2] if len(path.split("/")) > 2 else None
    elif path == "/playlist" or path.startswith("/playlist"):
        raise SourceError("YouTube playlists are not supported — add individual video URLs.")
    if not vid:
        if "list" in qs:
            raise SourceError("YouTube playlists are not supported — add individual video URLs.")
        raise SourceError("Could not find a video id in this YouTube URL.")
    if not _ID_RE.match(vid):
        raise SourceError(f"Invalid YouTube video id '{vid}'.")
    return f"https://www.youtube.com/watch?v={vid}", vid


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess helper
# ─────────────────────────────────────────────────────────────────────────────

# Only these variables reach yt-dlp / npx. App secrets (SECRET_KEY, API keys, DB URL…)
# are never inherited by third-party tools that run on untrusted input.
_ENV_ALLOW = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "LANG", "LC_ALL", "LC_CTYPE", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE",
)
# yt-dlp cookie configuration (e.g. YT_DLP_COOKIES=/path/cookies.txt, YTDLP_COOKIES_FROM_BROWSER)
_ENV_ALLOW_PREFIXES = ("YT_DLP_COOKIE", "YTDLP_COOKIE")
ANALYZER_PKG_DEFAULT = "mcp-video-analyzer@0.11.0"   # pinned; override COURSE_BUILDER_VIDEO_ANALYZER_PKG


def _subprocess_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        ku = k.upper()
        if ku in _ENV_ALLOW or ku.startswith(_ENV_ALLOW_PREFIXES):
            env[k] = v
    scripts = str(Path(sys.executable).parent)   # venv Scripts/bin → yt-dlp on PATH
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    env[path_key] = scripts + os.pathsep + env.get(path_key, "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _new_group_kwargs() -> dict:
    """Start the child in its own process group so the whole tree can be killed."""
    if os.name == "nt":
        import subprocess
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    try:
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/T", "/F", "/PID", str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=15)
        else:
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except Exception:
        pass


async def run_cmd(args: list[str], timeout: float, cwd: str | None = None) -> tuple[int, bytes, bytes]:
    """Run a command asynchronously with a hard timeout. Returns (code, stdout, stderr).
    The whole process tree is killed on timeout AND when the awaiting task is cancelled."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=_subprocess_env(),
        **_new_group_kwargs(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _kill_tree(proc)
        raise SourceError(f"Command timed out after {int(timeout)}s: {Path(args[0]).name}")
    except asyncio.CancelledError:
        await asyncio.shield(_kill_tree(proc))
        raise
    return proc.returncode or 0, out, err


def _ytdlp_cmd() -> list[str]:
    exe = shutil.which("yt-dlp", path=_subprocess_env()["PATH"])
    return [exe] if exe else [sys.executable, "-m", "yt_dlp"]


# ─────────────────────────────────────────────────────────────────────────────
# Preview
# ─────────────────────────────────────────────────────────────────────────────

async def preview_youtube(url: str) -> SourcePreview:
    canonical, vid = normalize_youtube_url(url)
    warnings: list[str] = []
    try:
        code, out, err = await run_cmd(
            _ytdlp_cmd() + ["-J", "--no-playlist", "--skip-download", "--no-warnings", canonical],
            timeout=_env_int("COURSE_BUILDER_YT_PREVIEW_TIMEOUT", 45),
        )
        if code == 0 and out.strip():
            info = json.loads(out.decode("utf-8", "replace"))
            return SourcePreview(
                type="youtube", url=canonical, kind="video",
                title=info.get("title") or vid,
                description=clip(info.get("description") or "", 500),
                author=info.get("uploader") or info.get("channel") or "",
                duration_seconds=info.get("duration"),
                thumbnail=info.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                extra={
                    "videoId": vid,
                    "hasCaptions": bool(info.get("subtitles") or info.get("automatic_captions")),
                    "chapters": [
                        {"time": c.get("start_time"), "title": c.get("title")}
                        for c in (info.get("chapters") or [])
                    ],
                },
            )
        warnings.append("yt-dlp metadata unavailable; using oEmbed (no duration).")
    except SourceError as exc:
        warnings.append(str(exc))
    except Exception as exc:  # yt-dlp missing, bad JSON …
        warnings.append(f"yt-dlp metadata failed: {exc}")

    async with http_client(timeout=15) as client:
        r = await client.get("https://www.youtube.com/oembed", params={"url": canonical, "format": "json"})
    if r.status_code != 200:
        raise SourceError(f"YouTube video not found or not embeddable (HTTP {r.status_code}).")
    data = r.json()
    return SourcePreview(
        type="youtube", url=canonical, kind="video",
        title=data.get("title") or vid,
        author=data.get("author_name") or "",
        thumbnail=data.get("thumbnail_url") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        extra={"videoId": vid},
        warnings=warnings,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fetch
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_youtube(url: str) -> list[SourceDocument]:
    canonical, vid = normalize_youtube_url(url)
    errors: list[str] = []

    if os.getenv("COURSE_BUILDER_YT_DISABLE_ANALYZER", "").lower() not in ("1", "true", "yes"):
        try:
            return [await _via_analyzer(canonical, vid)]
        except SourceError as exc:
            errors.append(f"mcp-video-analyzer: {exc}")
            log.warning("youtube analyzer failed for %s: %s", canonical, exc)

    try:
        return [await _via_ytdlp_captions(canonical, vid, errors)]
    except SourceError as exc:
        errors.append(f"yt-dlp captions: {exc}")
        log.warning("youtube captions failed for %s: %s", canonical, exc)

    try:
        return [await _via_whisper(canonical, vid, errors)]
    except SourceError as exc:
        errors.append(f"whisper: {exc}")

    raise SourceError("Could not get a transcript for this video. " + " | ".join(errors))


def _analyzer_cmd() -> list[str]:
    npx = shutil.which("npx")
    if not npx:
        raise SourceError("npx (Node.js) not found on PATH.")
    pkg = os.getenv("COURSE_BUILDER_VIDEO_ANALYZER_PKG", ANALYZER_PKG_DEFAULT)
    return [npx, "-y", pkg]


def _extract_json(stdout: bytes) -> dict[str, Any]:
    text = stdout.decode("utf-8", "replace").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise SourceError("analyzer produced no JSON output")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SourceError(f"analyzer output is not valid JSON: {exc}") from exc


async def _via_analyzer(canonical: str, vid: str) -> SourceDocument:
    max_frames = _env_int("COURSE_BUILDER_YT_MAX_FRAMES", 8)
    timeout = _env_int("COURSE_BUILDER_YT_TIMEOUT", 900)
    with tempfile.TemporaryDirectory(prefix="bookey_yt_") as tmp:
        code, out, err = await run_cmd(
            _analyzer_cmd() + [
                "analyze", canonical,
                "--detail", "standard",
                "--fields", "metadata,transcript,chapters,timeline",
                "--max-frames", str(max_frames),
            ],
            timeout=timeout,
            cwd=tmp,
        )
        if code != 0:
            tail = err.decode("utf-8", "replace").strip().splitlines()[-3:]
            raise SourceError(f"exit {code}: {' / '.join(tail)[:400]}")
        data = _extract_json(out)
    return analyzer_json_to_document(data, canonical, vid)


def analyzer_json_to_document(data: dict[str, Any], canonical: str, vid: str) -> SourceDocument:
    """Map mcp-video-analyzer JSON → SourceDocument (pure; unit-tested)."""
    meta = data.get("metadata") or {}
    transcript = data.get("transcript") or []
    segments: list[TimedSegment] = []
    for t in transcript:
        if not isinstance(t, dict):
            continue
        txt = (t.get("text") or "").strip()
        if not txt:
            continue
        segments.append(TimedSegment(
            start=parse_ts(t.get("time", t.get("start"))) or 0.0,
            end=parse_ts(t.get("endTime", t.get("end"))),
            text=txt,
        ))
    ocr_lines: list[str] = []
    for o in data.get("ocrResults") or []:
        if isinstance(o, dict) and (o.get("text") or "").strip():
            ocr_lines.append(f"- [{fmt_ts(parse_ts(o.get('time')))}] {o['text'].strip()}")
    if not segments and not ocr_lines:
        raise SourceError("analyzer returned no transcript (video may have no captions)")

    images = []
    for f in data.get("frames") or []:
        if isinstance(f, dict) and f.get("filePath"):
            images.append(SourceImage(time=parse_ts(f.get("time")), path=f["filePath"],
                                      mime_type=f.get("mimeType") or "image/jpeg"))

    chapters = [c for c in (data.get("chapters") or []) if isinstance(c, dict)]
    return _build_doc(
        canonical, vid,
        title=meta.get("title") or f"YouTube video {vid}",
        author=meta.get("uploader") or meta.get("channel") or "",
        description=meta.get("description") or "",
        duration=meta.get("duration"),
        thumbnail=meta.get("thumbnailUrl"),
        chapters=[{"time": parse_ts(c.get("time")), "title": c.get("title") or ""} for c in chapters],
        segments=segments,
        ocr_lines=ocr_lines,
        images=images,
        method="mcp-video-analyzer",
        warnings=[str(w) for w in (data.get("warnings") or [])],
    )


def _build_doc(
    canonical: str, vid: str, *, title: str, author: str, description: str,
    duration: float | None, thumbnail: str | None, chapters: list[dict],
    segments: list[TimedSegment], ocr_lines: list[str], images: list[SourceImage],
    method: str, warnings: list[str] | None = None,
) -> SourceDocument:
    lines = [f"# {title}", "", f"Source: YouTube video {canonical}"]
    if author:
        lines.append(f"Channel: {author}")
    if duration:
        lines.append(f"Duration: {fmt_ts(duration)}")
    if description:
        lines += ["", "## Description", "", clip(description, 3000)]
    if chapters:
        lines += ["", "## Chapters", ""]
        lines += [f"- [{fmt_ts(c['time'])}] {c['title']}" for c in chapters]

    # Transcript grouped under chapter headings (if any) so chunks carry headings.
    lines += ["", "## Transcript", ""]
    chap_iter = sorted([c for c in chapters if c.get("time") is not None], key=lambda c: c["time"])
    ci = 0
    for seg in segments:
        while ci < len(chap_iter) and seg.start >= chap_iter[ci]["time"]:
            lines += ["", f"### {chap_iter[ci]['title'] or 'Section'} ({fmt_ts(chap_iter[ci]['time'])})", ""]
            ci += 1
        lines.append(f"[{fmt_ts(seg.start)}] {seg.text}")
    if ocr_lines:
        lines += ["", "## On-screen text (OCR)", ""] + ocr_lines

    return SourceDocument(
        title=title,
        url=canonical,
        kind="youtube",
        text="\n".join(lines).strip() + "\n",
        metadata={
            "videoId": vid, "author": author, "durationSeconds": duration,
            "thumbnail": thumbnail or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "chapters": chapters, "method": method, "warnings": warnings or [],
            "segmentCount": len(segments),
        },
        timestamps=segments,
        images=images,
    )


# ── fallback 1: yt-dlp captions ─────────────────────────────────────────────

_VTT_TS = re.compile(r"(\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}\s+-->\s+((\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})")
_TAG_RE = re.compile(r"<[^>]+>")


def parse_vtt(vtt: str) -> list[TimedSegment]:
    """WebVTT → de-duplicated segments (auto-captions repeat rolling lines)."""
    out: list[TimedSegment] = []
    seen_last = ""
    blocks = re.split(r"\n\s*\n", vtt.replace("\r\n", "\n"))
    for block in blocks:
        lines = [l for l in block.strip().split("\n") if l.strip()]
        ts_idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ts_idx is None:
            continue
        start_s, _, rest = lines[ts_idx].partition("-->")
        end_s = rest.strip().split(" ")[0]
        start = parse_ts(start_s.strip()) or 0.0
        end = parse_ts(end_s)
        for raw in lines[ts_idx + 1 :]:
            txt = _TAG_RE.sub("", raw).strip()
            txt = re.sub(r"\s+", " ", txt)
            if not txt or txt == seen_last:
                continue
            if out and txt.startswith(out[-1].text) and len(txt) > len(out[-1].text):
                out[-1].text = txt
                out[-1].end = end
            else:
                out.append(TimedSegment(start=start, end=end, text=txt))
            seen_last = txt
    return out


async def _ytdlp_info(canonical: str, tmp: str, extra: list[str], timeout: int) -> dict[str, Any]:
    code, out, err = await run_cmd(
        _ytdlp_cmd() + ["--no-playlist", "--no-warnings", "--write-info-json",
                        "-o", os.path.join(tmp, "%(id)s.%(ext)s")] + extra + [canonical],
        timeout=timeout,
    )
    if code != 0:
        tail = err.decode("utf-8", "replace").strip().splitlines()[-2:]
        raise SourceError(f"yt-dlp exit {code}: {' / '.join(tail)[:400]}")
    info_files = list(Path(tmp).glob("*.info.json"))
    if not info_files:
        return {}
    return json.loads(info_files[0].read_text("utf-8", "replace"))


async def _via_ytdlp_captions(canonical: str, vid: str, errors: list[str]) -> SourceDocument:
    langs = os.getenv("COURSE_BUILDER_YT_SUB_LANGS", "en.*,en,ta.*,ta,hi.*")
    with tempfile.TemporaryDirectory(prefix="bookey_ytsub_") as tmp:
        info = await _ytdlp_info(
            canonical, tmp,
            ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", langs, "--sub-format", "vtt"],
            timeout=_env_int("COURSE_BUILDER_YT_SUB_TIMEOUT", 180),
        )
        vtts = sorted(Path(tmp).glob("*.vtt"), key=lambda p: (".en" not in p.name, p.name))
        if not vtts:
            raise SourceError("no captions available")
        segments = parse_vtt(vtts[0].read_text("utf-8", "replace"))
    if not segments:
        raise SourceError("captions file was empty")
    return _build_doc(
        canonical, vid,
        title=info.get("title") or f"YouTube video {vid}",
        author=info.get("uploader") or "", description=info.get("description") or "",
        duration=info.get("duration"), thumbnail=info.get("thumbnail"),
        chapters=[{"time": c.get("start_time"), "title": c.get("title") or ""} for c in info.get("chapters") or []],
        segments=segments, ocr_lines=[], images=[], method="yt-dlp-captions", warnings=errors[:],
    )


# ── fallback 2: audio + faster-whisper ──────────────────────────────────────

async def _via_whisper(canonical: str, vid: str, errors: list[str]) -> SourceDocument:
    try:
        from app.voice.stt.faster_whisper import get_whisper_model  # lazy, optional
    except Exception as exc:
        raise SourceError(f"faster-whisper not available ({exc})")
    max_s = _env_int("COURSE_BUILDER_YT_MAX_WHISPER_SECONDS", 1800)
    with tempfile.TemporaryDirectory(prefix="bookey_ytaud_") as tmp:
        info = await _ytdlp_info(
            canonical, tmp,
            ["-f", "bestaudio/best", "--match-filter", f"duration <= {max_s}"],
            timeout=_env_int("COURSE_BUILDER_YT_AUDIO_TIMEOUT", 600),
        )
        audio = [p for p in Path(tmp).iterdir() if not p.name.endswith(".json")]
        if not audio:
            raise SourceError(f"audio download failed or video longer than {max_s}s")
        model_name = os.getenv("COURSE_BUILDER_WHISPER_MODEL", "small")

        def _transcribe() -> list[TimedSegment]:
            model = get_whisper_model(model_name)
            if model is None:
                raise SourceError("faster-whisper package is not installed")
            segs, _info = model.transcribe(str(audio[0]), task="transcribe", vad_filter=True)
            return [TimedSegment(start=s.start, end=s.end, text=s.text.strip()) for s in segs if s.text.strip()]

        segments = await asyncio.to_thread(_transcribe)
    if not segments:
        raise SourceError("whisper produced an empty transcript")
    return _build_doc(
        canonical, vid,
        title=info.get("title") or f"YouTube video {vid}",
        author=info.get("uploader") or "", description=info.get("description") or "",
        duration=info.get("duration"), thumbnail=info.get("thumbnail"),
        chapters=[{"time": c.get("start_time"), "title": c.get("title") or ""} for c in info.get("chapters") or []],
        segments=segments, ocr_lines=[], images=[], method="faster-whisper", warnings=errors[:],
    )
