"""
app/api/search.py — Full-text search over course pages.

GET /search?q=&courseId= — returns ranked page excerpts

Implementation:
  SQLite     — FTS5 virtual table `pages_fts` (created at startup), bm25 ranking.
  PostgreSQL — to_tsvector('simple', title || narration_text) @@ plainto_tsquery('simple', q),
               GIN expression index ix_pages_fts (migration 0007), ts_rank + ts_headline.
  Fallback   — case-insensitive LIKE (any dialect) if the full-text query errors.

All results are filtered to courses the user can access (or public courses).
No auth required for public courses.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import optional_current_user
from app.db.database import get_db
from app.db.models import PAGES_TSVECTOR_SQL, User

router = APIRouter(tags=["search"])


class SearchResult(BaseModel):
    course_id: str
    page_id: str
    chapter_number: int
    title: str
    excerpt: str
    score: float


@router.get("/search", response_model=list[SearchResult])
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    courseId: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(optional_current_user),
) -> list[SearchResult]:
    """Full-text search over page titles and narration text (FTS5 / PostgreSQL tsvector)."""
    if not q.strip():
        return []

    # Sanitise query: remove FTS5 special chars that cause syntax errors
    safe_q = q.replace('"', "").replace("*", "").replace(":", "").strip()
    if not safe_q:
        return []

    is_admin = 1 if (user is not None and user.role == "admin") else 0
    params = {"q": safe_q, "course_id": courseId, "uid": user.id if user else "", "is_admin": is_admin}
    # Private learner books are filtered in SQL (before LIMIT) so they neither leak
    # nor crowd out visible results.
    visible = """
              AND (:is_admin = 1 OR p.course_id NOT IN (
                    SELECT lb.course_id FROM learner_books lb
                    WHERE lb.visibility = 'private' AND lb.owner_id != :uid))
    """
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    rows = None
    if dialect == "sqlite":
        try:
            sql = text(f"""
                SELECT
                    p.course_id,
                    p.id AS page_id,
                    c.number AS chapter_number,
                    p.title,
                    snippet(pages_fts, 1, '<em>', '</em>', '…', 10) AS excerpt,
                    bm25(pages_fts) AS score
                FROM pages_fts
                JOIN pages p ON pages_fts.rowid = p.rowid
                JOIN chapters c ON c.id = p.chapter_id
                WHERE pages_fts MATCH :q
                  AND (:course_id IS NULL OR p.course_id = :course_id)
                  {visible}
                ORDER BY score
                LIMIT 20
            """)
            rows = (await db.execute(sql, params)).fetchall()
        except Exception:
            rows = None  # FTS not set up / bad MATCH syntax → LIKE fallback
    elif dialect == "postgresql":
        tsv = PAGES_TSVECTOR_SQL.replace("coalesce(title", "coalesce(p.title").replace(
            "coalesce(narration_text", "coalesce(p.narration_text")
        try:
            sql = text(f"""
                SELECT
                    p.course_id,
                    p.id AS page_id,
                    c.number AS chapter_number,
                    p.title,
                    ts_headline('simple', coalesce(p.narration_text, ''), tq,
                                'StartSel=<em>, StopSel=</em>, MaxWords=12, MinWords=4') AS excerpt,
                    ts_rank({tsv}, tq) AS score
                FROM pages p
                JOIN chapters c ON c.id = p.chapter_id
                CROSS JOIN plainto_tsquery('simple', :q) AS tq
                WHERE {tsv} @@ tq
                  AND (CAST(:course_id AS VARCHAR) IS NULL OR p.course_id = CAST(:course_id AS VARCHAR))
                  {visible}
                ORDER BY score DESC, p.course_id, c.number, p.page_number
                LIMIT 20
            """)
            rows = (await db.execute(sql, params)).fetchall()
        except Exception:
            await db.rollback()
            rows = None
    if rows is None:
        rows = await _like_search(db, params, visible)
    return [
        SearchResult(
            course_id=row.course_id,
            page_id=row.page_id,
            chapter_number=row.chapter_number,
            title=row.title,
            excerpt=row.excerpt or "",
            score=float(row.score or 0.0),
        )
        for row in rows
    ]


async def _like_search(db: AsyncSession, params: dict, visible: str):
    """Portable fallback (PostgreSQL, or SQLite without FTS5): case-insensitive LIKE."""
    term = params["q"].lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
    sql = text(f"""
        SELECT p.course_id, p.id AS page_id, c.number AS chapter_number, p.title,
               substr(COALESCE(p.narration_text, ''), 1, 200) AS excerpt, 0.0 AS score
        FROM pages p
        JOIN chapters c ON c.id = p.chapter_id
        WHERE (lower(p.title) LIKE :pat ESCAPE '!' OR lower(COALESCE(p.narration_text, '')) LIKE :pat ESCAPE '!')
          AND (CAST(:course_id AS VARCHAR) IS NULL OR p.course_id = CAST(:course_id AS VARCHAR))
          {visible}
        ORDER BY p.course_id, c.number, p.page_number
        LIMIT 20
    """)
    try:
        return (await db.execute(sql, {**params, "pat": f"%{term}%"})).fetchall()
    except Exception:
        return []


async def refresh_search_index(conn_or_session) -> None:
    """Resync the SQLite FTS5 index with `pages` (call after pages are added/changed).
    No-op on other dialects (PostgreSQL's GIN expression index is maintained automatically)."""
    try:
        bind = getattr(conn_or_session, "bind", None) or conn_or_session
        if bind.dialect.name != "sqlite":
            return
        await conn_or_session.execute(text("INSERT INTO pages_fts(pages_fts) VALUES('rebuild')"))
    except Exception:
        pass  # FTS table missing (created at startup) — search falls back to LIKE
