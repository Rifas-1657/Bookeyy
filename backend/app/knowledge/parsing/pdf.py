"""
app/knowledge/parsing/pdf.py

PDF parser: pymupdf (fitz) primary, pypdf fallback.

OCR strategy (Phase-1):
- First attempt native text extraction via pymupdf / pypdf.
- If the extracted text is below _MIN_TEXT_PER_PAGE chars per page on average
  (i.e. the PDF is scanned or image-only), attempt OCR with pymupdf's built-in
  Tesseract binding (requires `pymupdf[ocr]` / tesseract installed).
- If Tesseract is unavailable, fall back to pypdf one more time; if that also
  yields too little text, raise ParseError with an actionable message.

OCR flag:
  Settings.ocr_enabled (default True) can be set to False in tests that
  don't have Tesseract installed.  OCR is attempted only when fitz is available.

Heading extraction:
  Bold large-font spans from pymupdf rawdict blocks are treated as headings.
  pypdf fallback path produces no heading metadata.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from app.knowledge.parsing import ParseError, ParsedPage

log = logging.getLogger(__name__)

_MIN_TEXT_PER_PAGE = 20        # chars; below this average → consider "scanned"
_OCR_LANG = "eng"              # Tesseract language; override via env if needed
_DEFAULT_MAX_PAGES = 300
_DEFAULT_MAX_OCR_PAGES = 30


def _page_limits() -> tuple[int, int]:
    """(max pages parsed, max pages OCR'd) — PDF_MAX_PAGES / PDF_MAX_OCR_PAGES."""
    try:
        from app.config import get_settings
        s = get_settings()
        return max(1, int(s.pdf_max_pages)), max(0, int(s.pdf_max_ocr_pages))
    except Exception:
        return _DEFAULT_MAX_PAGES, _DEFAULT_MAX_OCR_PAGES


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_headings_from_blocks(blocks: list[Any]) -> list[str]:
    """
    Heuristic heading extraction from pymupdf rawdict blocks.
    Bold or large-font spans near the top of the page → heading candidates.
    """
    headings: list[str] = []
    for block in blocks:
        if block.get("type") != 0:   # type 0 = text
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                flags = span.get("flags", 0)
                size  = span.get("size", 0)
                text  = span.get("text", "").strip()
                if not text:
                    continue
                is_bold = bool(flags & 2**4)   # bit 4 = bold in pymupdf flags
                if is_bold and size >= 11 and len(text) < 150:
                    headings.append(text)
    return headings


def _fitz_pages_to_parsed(doc: Any, *, ocr: bool = False) -> list[ParsedPage]:
    """
    Convert an open fitz.Document into ParsedPage list.
    When ocr=True, uses get_textpage_ocr() (Tesseract via fitz) on pages that
    have little or no extractable text.
    """
    import fitz  # noqa: PLC0415

    max_pages, max_ocr = _page_limits()
    limit = min(len(doc), max_ocr if ocr else max_pages)
    if len(doc) > limit:
        log.warning("PDF has %d pages; %s only the first %d", len(doc), "OCR'ing" if ocr else "parsing", limit)
    pages: list[ParsedPage] = []
    for page_index in range(limit):
        page = doc[page_index]

        if ocr:
            # get_textpage_ocr raises RuntimeError if Tesseract not installed
            tp = page.get_textpage_ocr(language=_OCR_LANG, dpi=200, full=True)
            text = tp.extractText().strip()
        else:
            text = page.get_text("text").strip()

        blocks = page.get_text("rawdict").get("blocks", [])
        headings = _extract_headings_from_blocks(blocks)

        pages.append(ParsedPage(
            page_number=page_index + 1,
            text=text,
            headings=headings,
        ))
    return pages


def _is_text_sparse(pages: list[ParsedPage]) -> bool:
    """Return True when the average text per page is too low to be a text PDF."""
    if not pages:
        return True
    total = sum(len(p.text) for p in pages)
    return total < _MIN_TEXT_PER_PAGE * len(pages)


def _parse_pdf_sync(data: bytes, ocr_enabled: bool = True) -> list[ParsedPage]:
    """
    Synchronous PDF parsing — called via asyncio.to_thread().

    Order:
      1. pymupdf native text extraction
      2. If sparse → pymupdf OCR (Tesseract) when ocr_enabled=True
      3. If fitz unavailable → pypdf
      4. If still sparse → ParseError
    """
    # ----------------------------------------------------------------
    # Attempt 1: pymupdf native
    # ----------------------------------------------------------------
    try:
        import fitz  # noqa: PLC0415

        doc = fitz.open(stream=data, filetype="pdf")
        if len(doc) == 0:
            doc.close()
            raise ParseError("PDF has no pages or could not be read.")

        pages = _fitz_pages_to_parsed(doc, ocr=False)

        if not _is_text_sparse(pages):
            doc.close()
            return pages

        # ----------------------------------------------------------------
        # Attempt 2: pymupdf + Tesseract OCR
        # ----------------------------------------------------------------
        if ocr_enabled:
            log.info(
                "PDF text is sparse (avg %.0f chars/page) — attempting OCR",
                sum(len(p.text) for p in pages) / max(len(pages), 1),
            )
            try:
                ocr_pages = _fitz_pages_to_parsed(doc, ocr=True)
                doc.close()
                if not _is_text_sparse(ocr_pages):
                    log.info("OCR succeeded: %d pages extracted", len(ocr_pages))
                    return ocr_pages
                # OCR ran but still sparse → fall through to error
                log.warning("OCR produced insufficient text; raising ParseError")
                raise ParseError(
                    "This PDF appears to be scanned or image-only. "
                    "OCR was attempted but produced insufficient text. "
                    "Please check that Tesseract is installed (apt-get install tesseract-ocr) "
                    "or provide a text-based PDF."
                )
            except RuntimeError as exc:
                # Tesseract not installed
                log.warning("Tesseract unavailable (%s); skipping OCR", exc)
                doc.close()
                # Fall through to pypdf and then final sparse-check
        else:
            doc.close()

        # No OCR or OCR disabled — sparse native extraction
        # Fall through to pypdf attempt before giving up

    except ImportError:
        log.debug("pymupdf (fitz) not available; using pypdf")

    # ----------------------------------------------------------------
    # Attempt 3: pypdf fallback
    # ----------------------------------------------------------------
    try:
        from pypdf import PdfReader  # noqa: PLC0415

        reader = PdfReader(io.BytesIO(data))
        pages_pypdf: list[ParsedPage] = []
        max_pages, _ = _page_limits()
        for page_index, page in enumerate(reader.pages):
            if page_index >= max_pages:
                log.warning("PDF has more than %d pages; parsing only the first %d", max_pages, max_pages)
                break
            text = (page.extract_text() or "").strip()
            pages_pypdf.append(ParsedPage(
                page_number=page_index + 1,
                text=text,
                headings=[],
            ))

        if len(pages_pypdf) == 0:
            raise ParseError("PDF has no pages or could not be read.")

        if _is_text_sparse(pages_pypdf):
            raise ParseError(
                "This PDF appears to be scanned or image-only (no extractable text). "
                "Install Tesseract (tesseract-ocr) to enable OCR, or upload a text-based PDF."
            )
        return pages_pypdf

    except ParseError:
        raise
    except ImportError:
        raise ParseError("No PDF library available. Install pymupdf: pip install pymupdf")
    except Exception as exc:
        raise ParseError(f"PDF parsing failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Public async entry-point
# ---------------------------------------------------------------------------

async def parse_pdf(data: bytes, ocr_enabled: bool | None = None) -> list[ParsedPage]:
    """
    Async wrapper: runs _parse_pdf_sync in a thread pool.

    ocr_enabled:
      None  → read from settings (default)
      True  → always attempt OCR on sparse PDFs
      False → never attempt OCR (useful for unit tests without Tesseract)
    """
    if ocr_enabled is None:
        try:
            from app.config import get_settings
            ocr_enabled = get_settings().ocr_enabled
        except Exception:
            ocr_enabled = True

    try:
        return await asyncio.to_thread(_parse_pdf_sync, data, ocr_enabled)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"PDF parsing failed: {exc}") from exc
