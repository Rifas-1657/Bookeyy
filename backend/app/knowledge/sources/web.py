"""
app/knowledge/sources/web.py — generic web page / docs, plus pasted syllabus text.

Fetching reuses the SSRF-safe `app.knowledge.url_fetcher.fetch_url` (HTML is stripped
there); PDFs/DOCX returned by a "web" URL are parsed with the existing parsers.
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlparse

from app.knowledge.parsing import ParseError, parse_document
from app.knowledge.sources.base import SourceDocument, SourceError, SourcePreview, clip, http_client
from app.knowledge.url_fetcher import FetchError, fetch_url, safe_stream, validate_url

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_RE = re.compile(r"<meta\s+[^>]*>", re.I)


def _meta(head: str, key: str) -> str | None:
    for tag in _META_RE.findall(head):
        if re.search(rf"""(property|name)\s*=\s*["']{re.escape(key)}["']""", tag, re.I):
            m = re.search(r"""content\s*=\s*["']([^"']*)["']""", tag, re.I)
            if m:
                return html.unescape(m.group(1)).strip()
    return None


async def _head_html(url: str, limit: int = 200_000) -> tuple[str, str, str]:
    """Fetch the first `limit` bytes of a page (SSRF-checked on every redirect hop).
    → (html, content_type, final_url)."""
    buf = b""
    try:
        async with http_client(timeout=15, follow_redirects=False) as client:
            async with safe_stream(client, url) as r:
                if r.status_code != 200:
                    raise SourceError(f"HTTP {r.status_code} fetching page.")
                ctype = r.headers.get("content-type", "")
                final = str(r.url)
                if "html" in ctype:
                    async for chunk in r.aiter_bytes():
                        buf += chunk
                        if len(buf) >= limit or b"</head>" in buf.lower():
                            break
    except FetchError as exc:
        raise SourceError(str(exc)) from exc
    return buf.decode("utf-8", "replace"), ctype, final


async def preview_web(url: str) -> SourcePreview:
    page, ctype, final = await _head_html(url)
    title = None
    m = _TITLE_RE.search(page)
    if m:
        title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
    title = _meta(page, "og:title") or title or (urlparse(final).hostname or url)
    return SourcePreview(
        type="web", url=final, title=title, kind="pdf" if "pdf" in ctype else "web page",
        description=clip(_meta(page, "og:description") or _meta(page, "description") or "", 400),
        author=_meta(page, "author") or _meta(page, "og:site_name") or "",
        thumbnail=_meta(page, "og:image"),
        extra={"contentType": ctype.split(";")[0]},
    )


async def fetch_web(url: str, title_hint: str | None = None) -> list[SourceDocument]:
    try:
        res = await fetch_url(url)
    except FetchError as exc:
        raise SourceError(str(exc)) from exc
    try:
        pages = await parse_document(res.data, res.mime_type, ocr_enabled=False)
    except ParseError as exc:
        raise SourceError(f"Could not parse page: {exc}") from exc
    body = "\n\n".join(p.text.strip() for p in pages if p.text.strip())
    if len(body) < 40:
        raise SourceError("Page has almost no readable text (it may require JavaScript).")
    title = title_hint
    if not title:
        try:
            title = (await preview_web(url)).title
        except Exception:
            title = urlparse(res.final_url).hostname or url
    text = f"# {title}\n\nSource: {res.final_url}\n\n{body}\n"
    return [SourceDocument(title=title, url=res.final_url, kind="web", text=text,
                           metadata={"mimeType": res.mime_type, "sizeBytes": res.content_length})]


def syllabus_document(text: str, title: str = "Course syllabus") -> SourceDocument:
    if not text or not text.strip():
        raise SourceError("Syllabus text is empty.")
    body = text.strip()
    if not body.lstrip().startswith("#"):
        body = f"# {title}\n\n{body}"
    return SourceDocument(title=title, url="", kind="syllabus", text=body + "\n",
                          metadata={"chars": len(text)})
