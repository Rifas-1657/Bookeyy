"""
app/knowledge/sources — multi-source adapters for the course builder.

    detect_source_type(url)            → "youtube" | "github" | ... | "web"
    await preview_source(type, url)    → SourcePreview   (cheap metadata)
    await fetch_source(type, url)      → list[SourceDocument]  (full text)

`ingest.ingest_source_documents()` stores the documents and indexes them with
the existing chunk + embed worker.
"""
from __future__ import annotations

from urllib.parse import urlparse

from app.knowledge.sources.base import (
    SOURCE_TYPES,
    SourceDocument,
    SourceError,
    SourcePreview,
    TimedSegment,
)

__all__ = [
    "SOURCE_TYPES", "SourceDocument", "SourceError", "SourcePreview", "TimedSegment",
    "detect_source_type", "preview_source", "fetch_source", "validate_source",
]


def detect_source_type(url: str) -> str:
    u = (url or "").strip()
    host = (urlparse(u).hostname or "").lower() if "://" in u else ""
    if not host:
        from app.knowledge.sources.papers import parse_arxiv_id
        try:
            parse_arxiv_id(u)
            return "arxiv"
        except SourceError:
            return "web"
    if host in ("youtu.be", "www.youtu.be") or host.endswith("youtube.com"):
        return "youtube"
    if host in ("github.com", "www.github.com"):
        return "github"
    if host == "redd.it" or host.endswith("reddit.com"):
        return "reddit"
    if host.endswith("arxiv.org"):
        return "arxiv"
    if urlparse(u).path.lower().endswith(".pdf"):
        return "pdf_url"
    return "web"


def validate_source(type_: str, url: str | None) -> str:
    """Cheap synchronous validation (no network). Returns the normalized URL."""
    if type_ not in SOURCE_TYPES:
        raise SourceError(f"Unknown source type '{type_}'.")
    if type_ in ("upload", "syllabus", "media", "images"):
        return url or ""
    if not url or not url.strip():
        raise SourceError("A URL is required for this source type.")
    url = url.strip()
    if type_ == "youtube":
        from app.knowledge.sources.youtube import normalize_youtube_url
        return normalize_youtube_url(url)[0]
    if type_ == "github":
        from app.knowledge.sources.github import parse_github_url
        parse_github_url(url)
        return url
    if type_ == "reddit":
        from app.knowledge.sources.reddit import normalize_reddit_url
        return normalize_reddit_url(url)
    if type_ == "arxiv":
        from app.knowledge.sources.papers import parse_arxiv_id
        return f"https://arxiv.org/abs/{parse_arxiv_id(url)}"
    from app.knowledge.url_fetcher import FetchError, validate_url
    try:
        validate_url(url)
    except FetchError as exc:
        raise SourceError(str(exc)) from exc
    return url


async def preview_source(type_: str | None, url: str) -> SourcePreview:
    t = type_ or detect_source_type(url)
    url = validate_source(t, url)
    if t == "youtube":
        from app.knowledge.sources.youtube import preview_youtube
        return await preview_youtube(url)
    if t == "github":
        from app.knowledge.sources.github import preview_github
        return await preview_github(url)
    if t == "reddit":
        from app.knowledge.sources.reddit import preview_reddit
        return await preview_reddit(url)
    if t == "arxiv":
        from app.knowledge.sources.papers import preview_arxiv
        return await preview_arxiv(url)
    if t == "pdf_url":
        from app.knowledge.sources.papers import preview_pdf_url
        return await preview_pdf_url(url)
    if t == "web":
        from app.knowledge.sources.web import preview_web
        return await preview_web(url)
    raise SourceError(f"Preview is not available for source type '{t}'.")


async def fetch_source(type_: str, url: str, *, title_hint: str | None = None) -> list[SourceDocument]:
    url = validate_source(type_, url)
    if type_ == "youtube":
        from app.knowledge.sources.youtube import fetch_youtube
        return await fetch_youtube(url)
    if type_ == "github":
        from app.knowledge.sources.github import fetch_github
        return await fetch_github(url)
    if type_ == "reddit":
        from app.knowledge.sources.reddit import fetch_reddit
        return await fetch_reddit(url)
    if type_ == "arxiv":
        from app.knowledge.sources.papers import fetch_arxiv
        return await fetch_arxiv(url)
    if type_ == "pdf_url":
        from app.knowledge.sources.papers import fetch_pdf_url
        return await fetch_pdf_url(url)
    if type_ == "web":
        from app.knowledge.sources.web import fetch_web
        return await fetch_web(url, title_hint=title_hint)
    raise SourceError(f"Source type '{type_}' is not fetched from a URL.")
