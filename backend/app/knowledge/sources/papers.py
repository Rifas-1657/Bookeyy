"""
app/knowledge/sources/papers.py — research papers.

  arXiv  : abs/pdf URL or bare id → arXiv Atom API metadata + PDF full text
           (parsed with the existing app.knowledge.parsing.pdf parser).
  pdf_url: any https PDF link, fetched via the SSRF-safe url_fetcher.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from app.knowledge.parsing import ParsedPage, ParseError
from app.knowledge.sources.base import SourceDocument, SourceError, SourcePreview, clip, http_client
from app.knowledge.url_fetcher import FetchError, check_host, fetch_url, validate_url

ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_NEW_ID = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
_OLD_ID = re.compile(r"([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?")
MAX_PDF_BYTES = 30 * 1024 * 1024


def parse_arxiv_id(value: str) -> str:
    v = value.strip()
    if v.lower().startswith("arxiv:"):
        v = v[6:]
    if v.startswith(("http://", "https://")):
        p = urlparse(v)
        if not (p.hostname or "").lower().endswith("arxiv.org"):
            raise SourceError("Not an arxiv.org URL.")
        v = re.sub(r"^/(abs|pdf|html)/", "", p.path)
        v = re.sub(r"\.pdf$", "", v).strip("/")
    m = _NEW_ID.fullmatch(v) or _OLD_ID.fullmatch(v)
    if not m:
        raise SourceError(f"Could not parse an arXiv id from '{value}'.")
    return m.group(1) + (m.group(2) or "")


async def _arxiv_meta(arxiv_id: str) -> dict:
    async with http_client(timeout=30) as client:
        r = await client.get(ARXIV_API, params={"id_list": arxiv_id, "max_results": "1"})
    if r.status_code != 200:
        raise SourceError(f"arXiv API error HTTP {r.status_code}.")
    return parse_arxiv_atom(r.text, arxiv_id)


def parse_arxiv_atom(xml_text: str, arxiv_id: str) -> dict:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError(f"arXiv API returned invalid XML: {exc}")
    entry = root.find("a:entry", _ATOM)
    if entry is None or entry.find("a:title", _ATOM) is None:
        raise SourceError(f"arXiv paper '{arxiv_id}' not found.")
    def _t(tag: str) -> str:
        el = entry.find(tag, _ATOM)
        return re.sub(r"\s+", " ", el.text or "").strip() if el is not None and el.text else ""
    title = _t("a:title")
    if not title or title.lower() == "error":
        raise SourceError(f"arXiv paper '{arxiv_id}' not found.")
    pdf = next((l.get("href") for l in entry.findall("a:link", _ATOM) if l.get("title") == "pdf"), None)
    return {
        "id": arxiv_id,
        "title": title,
        "summary": _t("a:summary"),
        "authors": [re.sub(r"\s+", " ", (a.findtext("a:name", "", _ATOM) or "")).strip()
                    for a in entry.findall("a:author", _ATOM)],
        "published": _t("a:published"),
        "updated": _t("a:updated"),
        "categories": [c.get("term") for c in entry.findall("a:category", _ATOM) if c.get("term")],
        "pdfUrl": (pdf or f"https://arxiv.org/pdf/{arxiv_id}").replace("http://", "https://"),
        "absUrl": f"https://arxiv.org/abs/{arxiv_id}",
    }


async def preview_arxiv(url: str) -> SourcePreview:
    meta = await _arxiv_meta(parse_arxiv_id(url))
    return SourcePreview(
        type="arxiv", url=meta["absUrl"], title=meta["title"], kind="paper",
        description=clip(meta["summary"], 600), author=", ".join(meta["authors"][:6]),
        extra={k: meta[k] for k in ("id", "published", "categories", "pdfUrl")},
    )


async def _pdf_pages(url: str) -> tuple[list[ParsedPage], int, str]:
    try:
        res = await fetch_url(url, max_bytes=MAX_PDF_BYTES)
    except FetchError as exc:
        raise SourceError(f"PDF download failed: {exc}") from exc
    if res.mime_type != "application/pdf":
        raise SourceError(f"URL did not return a PDF (got {res.mime_type}).")
    from app.knowledge.parsing.pdf import parse_pdf
    try:
        pages = await parse_pdf(res.data, ocr_enabled=False)
    except ParseError as exc:
        raise SourceError(f"PDF parse failed: {exc}") from exc
    return pages, len(res.data), res.final_url


def _pages_md(pages: list[ParsedPage]) -> list[str]:
    out: list[str] = []
    for p in pages:
        txt = (p.text or "").strip()
        if txt:
            out += ["", f"## Page {p.page_number}", "", txt]
    return out


async def fetch_arxiv(url: str) -> list[SourceDocument]:
    meta = await _arxiv_meta(parse_arxiv_id(url))
    lines = [f"# {meta['title']}", "", f"Source: arXiv {meta['absUrl']}",
             f"Authors: {', '.join(meta['authors'])}", f"Published: {meta['published'][:10]}",
             f"Categories: {', '.join(meta['categories'])}", "", "## Abstract", "", meta["summary"]]
    warnings: list[str] = []
    page_count = 0
    size = None
    try:
        pages, size, _ = await _pdf_pages(meta["pdfUrl"])
        page_count = len(pages)
        lines += _pages_md(pages)
    except SourceError as exc:
        warnings.append(f"Full text unavailable, abstract only: {exc}")
    return [SourceDocument(
        title=meta["title"], url=meta["absUrl"], kind="arxiv", text="\n".join(lines) + "\n",
        metadata={**meta, "pageCount": page_count, "sizeBytes": size, "warnings": warnings},
    )]


async def preview_pdf_url(url: str) -> SourcePreview:
    try:
        validate_url(url)
        await check_host(urlparse(url).hostname or "")
    except FetchError as exc:
        raise SourceError(str(exc)) from exc
    size = None
    ctype = ""
    # Preview never follows redirects (the real fetch validates every hop).
    async with http_client(timeout=15, follow_redirects=False) as client:
        try:
            r = await client.head(url)
            ctype = r.headers.get("content-type", "")
            size = int(r.headers["content-length"]) if r.headers.get("content-length", "").isdigit() else None
        except Exception:
            pass
    name = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or "document.pdf"
    warnings = [] if (not ctype or "pdf" in ctype or "octet-stream" in ctype) else [f"Server reports content-type '{ctype}'."]
    return SourcePreview(type="pdf_url", url=url, title=name, kind="pdf", size_bytes=size, warnings=warnings)


async def fetch_pdf_url(url: str) -> list[SourceDocument]:
    pages, size, final = await _pdf_pages(url)
    name = urlparse(final).path.rstrip("/").rsplit("/", 1)[-1] or "document.pdf"
    first_heading = next((h for p in pages for h in p.headings if h.strip()), "")
    title = first_heading[:180] or name
    lines = [f"# {title}", "", f"Source: PDF {final}"] + _pages_md(pages)
    return [SourceDocument(title=title, url=final, kind="pdf_url", text="\n".join(lines) + "\n",
                           metadata={"pageCount": len(pages), "sizeBytes": size, "filename": name})]
