"""
app/knowledge/parsing/__init__.py

DocumentParser interface + unified parse() factory function.

Parsed output is a list of ParsedPage objects — one per logical page/section.
Each contains:
  - page_number: int (1-based)
  - text: str
  - headings: list[str]  — heading path at the time of this page
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedPage:
    page_number: int
    text: str
    headings: list[str] = field(default_factory=list)


class ParseError(Exception):
    """Raised when a document cannot be parsed (e.g. scanned PDF with no text)."""


async def parse_document(
    data: bytes,
    mime_type: str,
    *,
    ocr_enabled: bool | None = None,
) -> list[ParsedPage]:
    """
    Parse *data* into a list of ParsedPage objects.
    Dispatches to the correct parser based on *mime_type*.
    Raises ParseError on failure.

    ocr_enabled:
      Passed through to the PDF parser only.
      None → read from settings (default); False → skip OCR (tests).
    """
    if mime_type == "application/pdf":
        from app.knowledge.parsing.pdf import parse_pdf
        return await parse_pdf(data, ocr_enabled=ocr_enabled)
    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        from app.knowledge.parsing.docx import parse_docx
        return await parse_docx(data)
    elif mime_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        from app.knowledge.parsing.pptx import parse_pptx
        return await parse_pptx(data)
    elif mime_type in ("text/plain", "text/markdown"):
        from app.knowledge.parsing.text import parse_text
        return await parse_text(data, mime_type)
    else:
        raise ParseError(f"Unsupported MIME type for parsing: {mime_type}")
