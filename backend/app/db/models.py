"""
app/db/models.py — SQLAlchemy 2 async ORM models.

Design goals:
- Written for PostgreSQL; SQLite works unchanged for dev/tests (via aiosqlite).
- No PostgreSQL-specific types in column definitions (uses Text, Integer, JSON
  instead of ARRAY or JSONB) so SQLite never fails.
- Every table uses a UUID primary key stored as a VARCHAR(36) for portability.

B1 additions:
- `password_hash` column on User
- `invites` table — admin-created invite tokens (hashed)
- `ws_tickets` table — short-lived single-use WS auth tickets (hashed)
- `password_reset_tokens` table
- `notifications` table
- `history` table (page view history)
- `tutor_settings` table (per-course admin tutor config)
- `search_index` FTS5-backed search (managed separately via raw SQL)
- Job.metadata renamed to Job.job_meta (attribute) to avoid SA warning

Phase-1 (source ingestion) additions:
- `content_hash` column on Document — SHA-256 of raw file bytes for dedup
- `source_type` column on Document — "file" | "url"
- `source_url` column on Document  — original URL for URL-sourced docs
- `source_job_id` column on Document — FK to SourceJob (nullable)
- `SourceJob` table — persistent job tracking for multi-file/URL ingestion batches
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC 'now' — the representation every DateTime column uses."""
    return datetime.utcnow()


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Users & Seats
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="learner")
    # B1: password hash (argon2id)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Bumped on logout / password reset; JWTs carry it as "tv" and are rejected on mismatch.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    seats: Mapped[list[Seat]] = relationship("Seat", back_populates="user")
    progress: Mapped[list[Progress]] = relationship("Progress", back_populates="user")
    bookmarks: Mapped[list[Bookmark]] = relationship("Bookmark", back_populates="user")
    notes: Mapped[list[Note]] = relationship("Note", back_populates="user")
    sessions: Mapped[list[Session]] = relationship("Session", back_populates="user")
    notifications: Mapped[list[Notification]] = relationship(
        "Notification", back_populates="user"
    )
    history: Mapped[list[History]] = relationship("History", back_populates="user")


class Seat(Base):
    """Which courses a user has access to."""
    __tablename__ = "seats"
    __table_args__ = (UniqueConstraint("user_id", "course_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship("User", back_populates="seats")
    course: Mapped[Course] = relationship("Course", back_populates="seats")


# ---------------------------------------------------------------------------
# Auth: Invites, WS Tickets, Password Reset Tokens
#
# All DateTime columns in this schema hold NAIVE UTC (datetime.utcnow()-style).
# Always compare against app.db.models.utcnow(), never an aware datetime.
# ---------------------------------------------------------------------------

class Invite(Base):
    """Admin-created invite tokens. User signs up with the raw token."""
    __tablename__ = "invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # SHA-256 hex of the raw token (never store raw)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="learner")
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    used_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WsTicket(Base):
    """Short-lived single-use WebSocket auth ticket (30 s TTL)."""
    __tablename__ = "ws_tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # SHA-256 hex of the raw ticket
    ticket_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Allowed origin (bound at issue time)
    origin: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PasswordResetToken(Base):
    """Password reset tokens. In dev, printed to log; in prod, emailed."""
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Courses, Chapters, Pages
# ---------------------------------------------------------------------------

class Course(Base):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # e.g. "python"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    subtitle: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    level: Mapped[str] = mapped_column(String(20), nullable=False, default="Beginner")
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="Programming")
    language: Mapped[str] = mapped_column(String(40), nullable=False, default="Python")
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    author: Mapped[str] = mapped_column(String(120), nullable=False, default="Bookey")
    cover: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    outcomes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    topics: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tutor_style: Mapped[str] = mapped_column(Text, nullable=False, default="")
    minutes_per_page: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chapters: Mapped[list[Chapter]] = relationship(
        "Chapter", back_populates="course", order_by="Chapter.number"
    )
    seats: Mapped[list[Seat]] = relationship("Seat", back_populates="course")
    progress: Mapped[list[Progress]] = relationship("Progress", back_populates="course")
    sessions: Mapped[list[Session]] = relationship("Session", back_populates="course")


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("course_id", "number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    course: Mapped[Course] = relationship("Course", back_populates="chapters")
    pages: Mapped[list[Page]] = relationship(
        "Page", back_populates="chapter", order_by="Page.page_number"
    )


# PostgreSQL full-text expression used by /search (GIN index ix_pages_fts, migration 0007).
# SQLite uses the FTS5 table `pages_fts` instead (created at startup).
PAGES_TSVECTOR_SQL = "to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(narration_text, ''))"


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("chapter_id", "page_number"),
        Index("ix_pages_fts", text(PAGES_TSVECTOR_SQL), postgresql_using="gin").ddl_if(dialect="postgresql"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[str] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    narration_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    paragraphs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    key_terms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    code_example: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    diagram_spec: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    cues: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    practice_prompts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    analogies: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    estimated_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chapter: Mapped[Chapter] = relationship("Chapter", back_populates="pages")
    progress: Mapped[list[Progress]] = relationship("Progress", back_populates="page")


# ---------------------------------------------------------------------------
# Progress, Bookmarks, Notes
# ---------------------------------------------------------------------------

class Progress(Base):
    __tablename__ = "progress"
    __table_args__ = (UniqueConstraint("user_id", "page_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    page_id: Mapped[str] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), nullable=False
    )
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_word_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_segment_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped[User] = relationship("User", back_populates="progress")
    course: Mapped[Course] = relationship("Course", back_populates="progress")
    page: Mapped[Page] = relationship("Page", back_populates="progress")


class Bookmark(Base):
    __tablename__ = "bookmarks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[str] = mapped_column(String(80), nullable=False)
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    page_id: Mapped[str] = mapped_column(String(80), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship("User", back_populates="bookmarks")


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[str] = mapped_column(String(80), nullable=False)
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    page_id: Mapped[str] = mapped_column(String(80), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped[User] = relationship("User", back_populates="notes")


class CourseSurvey(Base):
    """A learner's personalization answers for one course (one row per user+course)."""

    __tablename__ = "course_surveys"
    __table_args__ = (UniqueConstraint("user_id", "course_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_id: Mapped[str] = mapped_column(String(80), nullable=False)
    answers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    completed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# ---------------------------------------------------------------------------
# Sessions (narration sessions)
# ---------------------------------------------------------------------------

class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    page_id: Mapped[str] = mapped_column(String(80), nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_word_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_segment_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User | None] = relationship("User", back_populates="sessions")
    course: Mapped[Course] = relationship("Course", back_populates="sessions")


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # e.g. "invite", "system"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship("User", back_populates="notifications")


# ---------------------------------------------------------------------------
# History (page view history)
# ---------------------------------------------------------------------------

class History(Base):
    __tablename__ = "history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[str] = mapped_column(String(80), nullable=False)
    page_id: Mapped[str] = mapped_column(String(80), nullable=False)
    page_title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    visited_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship("User", back_populates="history")


# ---------------------------------------------------------------------------
# Admin: TutorSettings
# ---------------------------------------------------------------------------

class TutorSettings(Base):
    """Per-course tutor voice/speed/tone settings managed by admin."""
    __tablename__ = "tutor_settings"
    __table_args__ = (UniqueConstraint("course_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="tanglish")
    speed: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    tone: Mapped[str] = mapped_column(String(40), nullable=False, default="friendly")
    voice_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    custom_instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# ---------------------------------------------------------------------------
# B2: Knowledge base - Documents and Chunks
# ---------------------------------------------------------------------------

class SourceJob(Base):
    """
    Phase-1: Persistent job record for a multi-file/URL ingestion batch.

    One SourceJob is created per admin submission (many files or many URLs at
    once). Each Document in that batch carries a FK back here so the UI can
    poll the overall batch state with a single query.

    status lifecycle:
      queued → processing → done | failed | partial
        partial = at least one item succeeded and at least one failed.

    progress_pct is updated by the worker as items complete (0–100 integer).
    """
    __tablename__ = "source_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)      # "file" | "url" | "mixed"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    done_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="source_job", foreign_keys="[Document.source_job_id]"
    )
    submitter: Mapped["User"] = relationship("User", foreign_keys=[submitted_by])


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(260), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Phase-1 additions ---------------------------------------------------
    # SHA-256 hex of raw file/fetched bytes — used for duplicate detection.
    # Indexed; NULL until content is read (URL sources start NULL).
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # "file" (uploaded via multipart) or "url" (fetched by backend)
    source_type: Mapped[str] = mapped_column(String(10), nullable=False, default="file")
    # Original URL for URL-sourced documents; NULL for file uploads.
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FK to the SourceJob batch this document belongs to; NULL = standalone upload.
    source_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("source_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # --------------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        "DocumentChunk", back_populates="document", cascade="all, delete-orphan",
    )
    uploader: Mapped["User"] = relationship("User", foreign_keys=[uploaded_by])
    source_job: Mapped["SourceJob | None"] = relationship(
        "SourceJob", back_populates="documents", foreign_keys=[source_job_id]
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_model: Mapped[str] = mapped_column(String(120), nullable=False)
    vector_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    document: Mapped["Document"] = relationship("Document", back_populates="chunks")


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    page_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


JOB_ACTIVE_STATUSES = ("queued", "running")


class Job(Base):
    """
    Durable job queue row (app/jobs).  Shared by the API process (enqueue) and any
    number of workers (claim with a lease; see app/jobs/queue.py).

    status lifecycle:
      queued → running → succeeded
                       → queued (retry after backoff, attempts < max_attempts)
                       → failed (terminal: attempts exhausted / non-retryable)
      running with an expired lease is claimable again (crash recovery).

    `dedupe_key`: at most one queued/running job per key (partial unique index).
    """
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_after"),
        Index("ix_jobs_dedupe_key", "dedupe_key"),
        Index(
            "ux_jobs_active_dedupe", "dedupe_key", unique=True,
            postgresql_where=text("status IN ('queued', 'running') AND dedupe_key IS NOT NULL"),
            sqlite_where=text("status IN ('queued', 'running') AND dedupe_key IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    course_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # free-form metadata / result written by the handler
    job_meta: Mapped[dict] = mapped_column("job_meta", JSON, nullable=False, default=dict)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    run_after: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    locked_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# B3: Page generator — LLM usage, generated pages cache, gen logs
# ---------------------------------------------------------------------------

class LLMUsage(Base):
    """Per-call LLM usage counter. Aggregated per day/provider/model."""
    __tablename__ = "llm_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)   # ISO YYYY-MM-DD
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GeneratedPage(Base):
    """
    Cache for generated pages.
    Keyed by cache_key = sha256(course_id:chapter:page:phash:version)[:32].
    status: draft | published | unverified | archived
    """
    __tablename__ = "generated_pages"
    __table_args__ = {"extend_existing": True}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    content: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class PageGenLog(Base):
    """Records each generation attempt (for audit and debugging)."""
    __tablename__ = "page_gen_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    course_id: Mapped[str] = mapped_column(String(80), nullable=False)
    chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# B6: Personalization -- Thompson-sampling bandit
# ---------------------------------------------------------------------------

class BanditArm(Base):
    '''Per-(user, topic, arm) Beta distribution parameters for Thompson-sampling.'''
    __tablename__ = "bandit_arms"
    __table_args__ = (UniqueConstraint("user_id", "topic", "arm"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    arm: Mapped[int] = mapped_column(Integer, nullable=False)
    alpha: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    beta: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# B6: Personalization -- FSRS-5 revision scheduling
# ---------------------------------------------------------------------------

class FSRSCardModel(Base):
    '''Per-(user, topic) FSRS-5 card for spaced repetition.'''
    __tablename__ = "fsrs_cards"
    __table_args__ = (UniqueConstraint("user_id", "topic"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    stability: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    difficulty: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    last_review_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lapses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# R3: Adaptive learning -- learner model (migration 0008)
# ---------------------------------------------------------------------------

class LearnerConcept(Base):
    '''Per-(user, course, concept) mastery estimate (Bayesian Knowledge Tracing P(L)).

    See app/personalization/learner_model.py for the update rules.'''
    __tablename__ = "learner_concepts"
    __table_args__ = (UniqueConstraint("user_id", "course_id", "concept_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    concept_id: Mapped[str] = mapped_column(String(120), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    page_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    chapter_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mastery: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confusion: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class LearnerEvent(Base):
    '''Evidence log for the learner model (quiz answers, questions, "simpler", code runs, …).

    Also holds issued quiz items (kind="quiz_issued", answers in `meta`) and the teaching
    style shown for an explanation (kind="style_shown") so later rewards can be attributed.'''
    __tablename__ = "learner_events"
    __table_args__ = (Index("ix_learner_events_user_course_created", "user_id", "course_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    course_id: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    page_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    concept_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Course Builder — multi-source course authoring jobs (migration 0004)
# ---------------------------------------------------------------------------

class CourseBuildJob(Base):
    """
    One admin "build a course from sources" run.

    status lifecycle:
      queued → ingesting → outlining → generating → ready | failed
      (generating is re-entered when remaining lessons are generated later)

    `steps`, `sources` and `outline` are JSON documents owned by
    app/course_builder/runner.py (camelCase keys, returned as-is by the API).
    """
    __tablename__ = "course_build_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    sources: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    outline: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CourseBuildLesson(Base):
    """Generated page for one outline lesson of a CourseBuildJob."""
    __tablename__ = "course_build_lessons"
    __table_args__ = (UniqueConstraint("job_id", "lesson_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("course_build_jobs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    lesson_key: Mapped[str] = mapped_column(String(40), nullable=False)   # "ch1-l2"
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    lesson_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    page: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class LearnerBook(Base):
    """
    A book a learner generated from their own file / link / prompt (P1).

    Ownership + visibility for the Course row the job publishes into.  The row
    exists from job creation, so the course id is private before the first
    publish too.  `visibility="private"` books are only readable by the owner
    and admins (see app/books/access.py).
    """
    __tablename__ = "learner_books"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("course_build_jobs.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default="upload")  # upload|url|prompt|mixed
    source_label: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="private")
    ai_knowledge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
