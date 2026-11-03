"""
app/tutor/book_scope.py — topic scope for generated books (P1).

The seeded "python" course keeps the original Python-only guard.  Every other
course (admin course-builder books, learner books) is scoped to ITS OWN topic:
keywords come from the course title/subtitle/topics/outcomes and its pages'
titles + key terms, and the current page's text is loaded for grounding.

    await load_book_scope(course_id, user_id, chapter, page) → BookScope | None
        None  → use the legacy Python scope (course "python" or unknown course)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select

log = logging.getLogger(__name__)

LEGACY_PYTHON_COURSES = {"python"}

_STOP = frozenset("""
a an the and or of to in on for with from into by about as at is are be this that these those your you
it its how what why when which who learn learning book course chapter lesson lessons page pages part intro
introduction basics basic beginner beginners advanced intermediate guide getting started using use more
project mini work works working step steps core ideas practice common mistakes where next go matters
""".split())
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.\-]*", re.I)


@dataclass
class BookScope:
    course_id: str
    title: str
    language: str                      # "tanglish" | "english"
    topics: list[str] = field(default_factory=list)
    keywords: set[str] = field(default_factory=set)
    phrases: set[str] = field(default_factory=set)
    page_title: str = ""
    page_text: str = ""
    allowed: bool = True
    ai_generated: bool = False

    @property
    def summary(self) -> str:
        t = f"'{self.title}'"
        if self.topics:
            t += " — chapters: " + "; ".join(self.topics[:8])
        return t


def _words(text: str) -> set[str]:
    out = set()
    for w in _WORD.findall((text or "").lower()):
        w = w.strip(".-")
        if len(w) >= 3 and w not in _STOP and not w.isdigit():
            out.add(w)
            if w.endswith("s") and len(w) > 4:
                out.add(w[:-1])
    return out


def question_hits(question: str, scope: BookScope) -> str | None:
    q = question.lower()
    for ph in scope.phrases:
        if len(ph) >= 4 and ph in q:
            return ph
    for w in _words(q):
        if w in scope.keywords:
            return w
    return None


async def load_book_scope(course_id: str | None, user_id: str | None,
                          chapter: int | None = None, page: int | None = None) -> BookScope | None:
    if not course_id or course_id.lower() in LEGACY_PYTHON_COURSES:
        return None
    try:
        from app.books.access import user_can_view_course
        from app.db.database import AsyncSessionLocal
        from app.db.models import Chapter, Course, LearnerBook, Page

        async with AsyncSessionLocal() as db:
            course = await db.get(Course, course_id)
            if course is None:
                return None
            book = (await db.execute(select(LearnerBook).where(LearnerBook.course_id == course_id))
                    ).scalar_one_or_none()
            rows = (await db.execute(
                select(Page.title, Page.key_terms, Chapter.number, Page.page_number, Page.narration_text,
                       Page.paragraphs)
                .join(Chapter, Chapter.id == Page.chapter_id).where(Page.course_id == course_id)
            )).all()
        allowed = await user_can_view_course(course_id, user_id)
        lang = "tanglish" if (course.language or "").lower().startswith("tanglish") else "english"
        topics = [str(t) for t in (course.topics or [])]
        keywords = _words(" ".join([course.title, course.subtitle or "", course.description or ""]
                                   + topics + [str(o) for o in (course.outcomes or [])]))
        phrases: set[str] = {course.title.lower()}
        page_title = page_text = ""
        for title, terms, ch_no, p_no, narration, paragraphs in rows:
            keywords |= _words(title)
            for term in terms or []:
                term = str(term).strip().lower()
                if term:
                    phrases.add(term)
                    keywords |= _words(term)
            if chapter is not None and page is not None and ch_no == chapter and p_no == page:
                page_title = title
                page_text = " ".join([narration or ""] + [str(p) for p in (paragraphs or [])])
        return BookScope(course_id=course_id, title=course.title, language=lang, topics=topics,
                         keywords=keywords, phrases=phrases, page_title=page_title,
                         page_text=page_text[:4000], allowed=allowed,
                         ai_generated=bool(book and book.ai_knowledge))
    except Exception:
        log.debug("book scope load failed for %s", course_id, exc_info=True)
        return None
