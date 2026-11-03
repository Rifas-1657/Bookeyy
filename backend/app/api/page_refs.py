"""
app/api/page_refs.py — resolve a page id to its 1-based position in its chapter.

Bookmarks and notes store only page_id + chapter_number; the library UI also
needs the page's position inside the chapter to build deep links.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Page

# Seeded ids look like lesson_<course>_<chapter>_<page>.
_SEED_ID = re.compile(r"_(\d+)_(\d+)$")


async def page_numbers(db: AsyncSession, page_ids: set[str]) -> dict[str, int]:
    """Map each id to its page number; unknown ids fall back to the seed id pattern, then 1."""
    found: dict[str, int] = {}
    if page_ids:
        result = await db.execute(select(Page.id, Page.page_number).where(Page.id.in_(page_ids)))
        found = {pid: number for pid, number in result.all()}
    for pid in page_ids - found.keys():
        match = _SEED_ID.search(pid)
        found[pid] = int(match.group(2)) if match else 1
    return found
