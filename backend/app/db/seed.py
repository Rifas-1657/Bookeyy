"""
app/db/seed.py — seed the database from the frontend learning data.
Seeds: the public `python` course, from the book in app/content/python_book.json.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

import logging

from .models import Chapter, Course, Page

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Course seed — Python: The Complete Book
# ---------------------------------------------------------------------------

PYTHON_COURSE = {
    "id": "python",
    "title": "Python: The Complete Book",
    "subtitle": "From your first line of code to your own project, shaped around you.",
    "description": (
        "A complete Python book that rebuilds itself around you. Answer a few questions and Bookey "
        "picks your chapters, your pace and your examples, then teaches every page out loud."
    ),
    "level": "Beginner",
    "category": "Programming",
    "language": "Tanglish",
    "available": True,
    "author": "Bookey",
    "cover": {"base": "#E11D48", "spine": "#9F1239", "accent": "#FFE4E6", "text": "#FFFFFF"},
    "outcomes": [
        "Write and run short Python programs without copying from a template",
        "Choose the right data type and collection for a problem",
        "Control program flow with conditions and loops",
        "Split code into small, testable functions",
        "Read and save data to plain text files",
    ],
    "topics": ["Python 3", "Interactive code cells", "Variables", "Control flow", "Functions", "File I/O"],
    "tutor_style": "Patient and example-first: explains one idea at a time, then asks you to predict the output before running it.",
    "minutes_per_page": 4,
}

async def seed_database(session: AsyncSession) -> None:
    """Idempotent: publishes Bookey's Python book as the public `python` course, and replaces
    any earlier version of it (no admin or sample accounts are ever created here)."""
    from sqlalchemy import delete, select

    from app.books.personal import load_book, page_fields
    from app.content.python_curriculum import CHAPTERS

    course = await session.get(Course, "python")
    if course is not None and course.author == PYTHON_COURSE["author"]:
        return
    if course is not None:
        await session.execute(delete(Page).where(Page.course_id == "python"))
        await session.execute(delete(Chapter).where(Chapter.course_id == "python"))
        for key, value in PYTHON_COURSE.items():
            setattr(course, key, value)
    else:
        session.add(Course(**PYTHON_COURSE))
    await session.flush()

    book = load_book()
    answers = {"language": "tanglish", "pace": "balanced"}
    for number, title, summary, _tier, _track, lessons in CHAPTERS:
        chapter = Chapter(course_id="python", number=number, title=title, summary=summary,
                          page_count=len(lessons), locked=False)
        session.add(chapter)
        await session.flush()
        for i in range(len(lessons)):
            session.add(Page(id=f"lesson_python_{number}_{i + 1}", course_id="python", chapter_id=chapter.id,
                             page_number=i + 1, **page_fields(book[f"{number}.{i + 1}"], answers)))
    await session.commit()
