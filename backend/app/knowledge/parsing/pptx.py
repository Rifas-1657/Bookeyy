"""
app/knowledge/parsing/pptx.py

PPTX parser via python-pptx.

One ParsedPage per slide (empty slides are skipped). Each page contains:
  - the slide title (also used as the heading path, so chunks keep "which slide")
  - bullet / body text in reading order, indented by paragraph level
  - tables as " | "-separated rows
  - grouped shapes (recursively)
  - speaker notes under a "Speaker notes:" label (often the richest text in a deck)
"""
from __future__ import annotations

import asyncio
import io

from app.knowledge.parsing import ParseError, ParsedPage

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _shape_lines(shape, title_shape_id: int | None) -> list[str]:
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    lines: list[str] = []
    if getattr(shape, "shape_id", None) == title_shape_id:
        return lines
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for sub in shape.shapes:
            lines.extend(_shape_lines(sub, title_shape_id))
        return lines
    if getattr(shape, "has_table", False) and shape.has_table:
        for row in shape.table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
        return lines
    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        paras = shape.text_frame.paragraphs
        bulleted = len(paras) > 1
        for para in paras:
            text = para.text.strip()
            if not text:
                continue
            if bulleted or para.level:
                lines.append("  " * max(0, para.level) + "- " + text)
            else:
                lines.append(text)
    return lines


def _parse_pptx_sync(data: bytes) -> list[ParsedPage]:
    from app.knowledge.parsing.magic import check_zip_safety
    bomb = check_zip_safety(data)
    if bomb:
        raise ParseError(f"PPTX rejected: {bomb}")
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    pages: list[ParsedPage] = []
    for number, slide in enumerate(prs.slides, start=1):
        title_shape = slide.shapes.title
        title = (title_shape.text_frame.text.strip() if title_shape is not None and title_shape.has_text_frame
                 else "")
        title_id = title_shape.shape_id if title_shape is not None else None
        body: list[str] = []
        for shape in slide.shapes:
            body.extend(_shape_lines(shape, title_id))
        notes = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()

        parts: list[str] = []
        if title:
            parts.append(f"# {title}")
        parts.extend(body)
        if notes:
            parts.append(f"Speaker notes: {notes}")
        text = "\n".join(parts).strip()
        if not text:
            continue
        pages.append(ParsedPage(page_number=number, text=text,
                                headings=[title or f"Slide {number}"]))
    if not pages:
        raise ParseError("PPTX presentation contains no extractable text.")
    return pages


async def parse_pptx(data: bytes) -> list[ParsedPage]:
    try:
        return await asyncio.to_thread(_parse_pptx_sync, data)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"PPTX parsing failed: {exc}") from exc
