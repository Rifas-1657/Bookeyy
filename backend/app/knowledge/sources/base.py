"""
app/knowledge/sources/base.py — shared types for multi-source ingestion.

Every adapter turns one admin-supplied source (YouTube video, GitHub repo,
Reddit thread, arXiv paper, PDF URL, web page, syllabus text) into one or
more normalized `SourceDocument`s.  Those documents are then stored as
Markdown and fed through the EXISTING parse → chunk → embed pipeline
(`app.knowledge.worker.process_document`) by `app.knowledge.sources.ingest`.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import httpx

SourceType = Literal["youtube", "github", "reddit", "arxiv", "pdf_url", "web", "upload", "syllabus", "media", "images"]
SOURCE_TYPES: tuple[str, ...] = ("youtube", "github", "reddit", "arxiv", "pdf_url", "web", "upload", "syllabus",
                                 "media", "images")
# Sources built from stored uploads (no URL): document / video-audio file / batch of images.
UPLOAD_SOURCE_TYPES: tuple[str, ...] = ("upload", "media", "images")

DEFAULT_UA = os.getenv(
    "COURSE_BUILDER_USER_AGENT",
    "BookeyCourseBuilder/1.0 (+https://bookey.app; educational course builder)",
)


class SourceError(Exception):
    """Raised when a source cannot be previewed or fetched. Message is admin-facing."""


@dataclass
class TimedSegment:
    start: float            # seconds
    end: float | None
    text: str


@dataclass
class SourceImage:
    time: float | None      # seconds into a video, if applicable
    path: str               # local path or storage key
    mime_type: str = "image/jpeg"


@dataclass
class SourceDocument:
    """A normalized text document ready for chunking."""
    title: str
    url: str
    kind: str                               # one of SOURCE_TYPES
    text: str                               # Markdown
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamps: list[TimedSegment] = field(default_factory=list)
    images: list[SourceImage] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourcePreview:
    """Cheap metadata shown in the admin UI before a full ingest."""
    type: str
    url: str
    title: str
    kind: str                               # human label: "video", "repository", ...
    description: str = ""
    author: str = ""
    duration_seconds: float | None = None
    size_bytes: int | None = None
    thumbnail: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_api(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "url": self.url,
            "title": self.title,
            "kind": self.kind,
            "description": self.description,
            "author": self.author,
            "durationSeconds": self.duration_seconds,
            "sizeBytes": self.size_bytes,
            "thumbnail": self.thumbnail,
            "extra": self.extra,
            "warnings": self.warnings,
        }


# Hosts the fixed-endpoint adapters talk to (GitHub / Reddit / arXiv / YouTube oEmbed).
# Requests to any OTHER host (e.g. via a redirect) get a full DNS → non-global-IP check.
TRUSTED_SOURCE_HOST_SUFFIXES: tuple[str, ...] = (
    "github.com", "githubusercontent.com", "reddit.com", "redd.it", "arxiv.org", "youtube.com",
)


def _is_trusted_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == s or host.endswith("." + s) for s in TRUSTED_SOURCE_HOST_SUFFIXES)


async def _ssrf_request_hook(request: httpx.Request) -> None:
    """Runs for every request the client sends, including each redirect hop."""
    from app.knowledge.url_fetcher import FetchError, check_host, validate_url
    url = str(request.url)
    try:
        validate_url(url)
        host = request.url.host
        if not _is_trusted_host(host):
            await check_host(host)
    except FetchError as exc:
        raise SourceError(str(exc)) from exc


def http_client(
    *,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    h = {"User-Agent": DEFAULT_UA}
    if headers:
        h.update(headers)
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10.0),
        headers=h,
        follow_redirects=follow_redirects,
        max_redirects=5,
        event_hooks={"request": [_ssrf_request_hook]},
    )


def clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def fmt_ts(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def parse_ts(value: Any) -> float | None:
    """'1:02:03' | '2:03' | '12.5' | 12 → seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", ".")
    if not s:
        return None
    try:
        parts = [float(p) for p in s.split(":")]
    except ValueError:
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total
