"""Baseline: all tables that existed before Phase-1 source ingestion.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-21 00:00:00.000000

This migration captures the schema that was previously auto-created by
Base.metadata.create_all() on startup.  Running `alembic upgrade head` on a
brand-new database will apply both this and the Phase-1 delta in order.
On an existing database that was bootstrapped by create_all, Alembic will
detect the tables as present (via alembic_version) and skip to the delta.

To bring an existing database under Alembic control without re-creating tables:
    alembic stamp 0001_baseline   # mark as applied without running DDL
    alembic upgrade head          # apply only the Phase-1 delta forward
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id",           sa.String(36),  primary_key=True),
        sa.Column("email",        sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("avatar_url",   sa.Text(),       nullable=True),
        sa.Column("role",         sa.String(20),  nullable=False, server_default="learner"),
        sa.Column("password_hash",sa.Text(),       nullable=False, server_default=""),
        sa.Column("is_active",    sa.Boolean(),   nullable=False, server_default=sa.true()),
        sa.Column("created_at",   sa.DateTime(),  nullable=True),
        sa.Column("updated_at",   sa.DateTime(),  nullable=True),
    )

    # ── courses ───────────────────────────────────────────────────────────────
    op.create_table(
        "courses",
        sa.Column("id",              sa.String(80),  primary_key=True),
        sa.Column("title",           sa.String(200), nullable=False),
        sa.Column("subtitle",        sa.String(400), nullable=False, server_default=""),
        sa.Column("description",     sa.Text(),      nullable=False, server_default=""),
        sa.Column("level",           sa.String(20),  nullable=False, server_default="Beginner"),
        sa.Column("category",        sa.String(80),  nullable=False, server_default="Programming"),
        sa.Column("language",        sa.String(40),  nullable=False, server_default="Python"),
        sa.Column("available",       sa.Boolean(),   nullable=False, server_default=sa.true()),
        sa.Column("author",          sa.String(120), nullable=False, server_default="Bookey"),
        sa.Column("cover",           sa.JSON(),      nullable=False),
        sa.Column("outcomes",        sa.JSON(),      nullable=False),
        sa.Column("topics",          sa.JSON(),      nullable=False),
        sa.Column("tutor_style",     sa.Text(),      nullable=False, server_default=""),
        sa.Column("minutes_per_page",sa.Integer(),   nullable=False, server_default="4"),
        sa.Column("created_at",      sa.DateTime(),  nullable=True),
    )

    # ── seats ─────────────────────────────────────────────────────────────────
    op.create_table(
        "seats",
        sa.Column("id",         sa.String(36), primary_key=True),
        sa.Column("user_id",    sa.String(36), sa.ForeignKey("users.id",   ondelete="CASCADE"), nullable=False),
        sa.Column("course_id",  sa.String(80), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "course_id"),
    )

    # ── invites ───────────────────────────────────────────────────────────────
    op.create_table(
        "invites",
        sa.Column("id",             sa.String(36),  primary_key=True),
        sa.Column("token_hash",     sa.String(64),  nullable=False, unique=True),
        sa.Column("invited_email",  sa.String(320), nullable=True),
        sa.Column("role",           sa.String(20),  nullable=False, server_default="learner"),
        sa.Column("created_by",     sa.String(36),  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("used_by",        sa.String(36),  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at",     sa.DateTime(),  nullable=False),
        sa.Column("used_at",        sa.DateTime(),  nullable=True),
        sa.Column("created_at",     sa.DateTime(),  nullable=True),
    )

    # ── ws_tickets ────────────────────────────────────────────────────────────
    op.create_table(
        "ws_tickets",
        sa.Column("id",          sa.String(36), primary_key=True),
        sa.Column("ticket_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id",     sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("origin",      sa.String(200),nullable=False, server_default=""),
        sa.Column("expires_at",  sa.DateTime(), nullable=False),
        sa.Column("created_at",  sa.DateTime(), nullable=True),
    )

    # ── password_reset_tokens ─────────────────────────────────────────────────
    op.create_table(
        "password_reset_tokens",
        sa.Column("id",         sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id",    sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at",    sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    # ── chapters ──────────────────────────────────────────────────────────────
    op.create_table(
        "chapters",
        sa.Column("id",        sa.String(36),  primary_key=True),
        sa.Column("course_id", sa.String(80),  sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number",    sa.Integer(),   nullable=False),
        sa.Column("title",     sa.String(200), nullable=False),
        sa.Column("summary",   sa.Text(),      nullable=False, server_default=""),
        sa.Column("page_count",sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("locked",    sa.Boolean(),   nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("course_id", "number"),
    )

    # ── pages ─────────────────────────────────────────────────────────────────
    op.create_table(
        "pages",
        sa.Column("id",               sa.String(80),  primary_key=True),
        sa.Column("course_id",        sa.String(80),  sa.ForeignKey("courses.id",   ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id",       sa.String(36),  sa.ForeignKey("chapters.id",  ondelete="CASCADE"), nullable=False),
        sa.Column("page_number",      sa.Integer(),   nullable=False),
        sa.Column("title",            sa.String(200), nullable=False),
        sa.Column("narration_text",   sa.Text(),      nullable=False, server_default=""),
        sa.Column("paragraphs",       sa.JSON(),      nullable=False),
        sa.Column("key_terms",        sa.JSON(),      nullable=False),
        sa.Column("code_example",     sa.JSON(),      nullable=False),
        sa.Column("diagram_spec",     sa.JSON(),      nullable=False),
        sa.Column("cues",             sa.JSON(),      nullable=False),
        sa.Column("practice_prompts", sa.JSON(),      nullable=False),
        sa.Column("analogies",        sa.JSON(),      nullable=False),
        sa.Column("estimated_minutes",sa.Integer(),   nullable=False, server_default="4"),
        sa.Column("created_at",       sa.DateTime(),  nullable=True),
        sa.UniqueConstraint("chapter_id", "page_number"),
    )

    # ── progress ──────────────────────────────────────────────────────────────
    op.create_table(
        "progress",
        sa.Column("id",              sa.String(36), primary_key=True),
        sa.Column("user_id",         sa.String(36), sa.ForeignKey("users.id",   ondelete="CASCADE"), nullable=False),
        sa.Column("course_id",       sa.String(80), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_id",         sa.String(80), sa.ForeignKey("pages.id",   ondelete="CASCADE"), nullable=False),
        sa.Column("completed",       sa.Boolean(),  nullable=False, server_default=sa.false()),
        sa.Column("last_word_index", sa.Integer(),  nullable=False, server_default="0"),
        sa.Column("last_segment_id", sa.String(120),nullable=True),
        sa.Column("completed_at",    sa.DateTime(), nullable=True),
        sa.Column("updated_at",      sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "page_id"),
    )

    # ── bookmarks ─────────────────────────────────────────────────────────────
    op.create_table(
        "bookmarks",
        sa.Column("id",             sa.String(36), primary_key=True),
        sa.Column("user_id",        sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id",      sa.String(80), nullable=False),
        sa.Column("chapter_number", sa.Integer(),  nullable=False),
        sa.Column("page_id",        sa.String(80), nullable=False),
        sa.Column("excerpt",        sa.Text(),     nullable=False, server_default=""),
        sa.Column("created_at",     sa.DateTime(), nullable=True),
    )

    # ── notes ─────────────────────────────────────────────────────────────────
    op.create_table(
        "notes",
        sa.Column("id",             sa.String(36), primary_key=True),
        sa.Column("user_id",        sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id",      sa.String(80), nullable=False),
        sa.Column("chapter_number", sa.Integer(),  nullable=False),
        sa.Column("page_id",        sa.String(80), nullable=False),
        sa.Column("text",           sa.Text(),     nullable=False, server_default=""),
        sa.Column("created_at",     sa.DateTime(), nullable=True),
        sa.Column("updated_at",     sa.DateTime(), nullable=True),
    )

    # ── sessions ──────────────────────────────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column("id",              sa.String(36),  primary_key=True),
        sa.Column("user_id",         sa.String(36),  sa.ForeignKey("users.id",   ondelete="SET NULL"), nullable=True),
        sa.Column("course_id",       sa.String(80),  sa.ForeignKey("courses.id", ondelete="CASCADE"),  nullable=False),
        sa.Column("page_id",         sa.String(80),  nullable=False),
        sa.Column("last_seq",        sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("last_word_index", sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("last_segment_id", sa.String(120), nullable=True),
        sa.Column("started_at",      sa.DateTime(),  nullable=True),
        sa.Column("ended_at",        sa.DateTime(),  nullable=True),
    )

    # ── notifications ─────────────────────────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("id",         sa.String(36),  primary_key=True),
        sa.Column("user_id",    sa.String(36),  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind",       sa.String(40),  nullable=False),
        sa.Column("title",      sa.String(200), nullable=False),
        sa.Column("body",       sa.Text(),      nullable=False, server_default=""),
        sa.Column("read",       sa.Boolean(),   nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(),  nullable=True),
    )

    # ── history ───────────────────────────────────────────────────────────────
    op.create_table(
        "history",
        sa.Column("id",         sa.String(36),  primary_key=True),
        sa.Column("user_id",    sa.String(36),  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id",  sa.String(80),  nullable=False),
        sa.Column("page_id",    sa.String(80),  nullable=False),
        sa.Column("page_title", sa.String(200), nullable=False, server_default=""),
        sa.Column("visited_at", sa.DateTime(),  nullable=True),
    )

    # ── tutor_settings ────────────────────────────────────────────────────────
    op.create_table(
        "tutor_settings",
        sa.Column("id",                  sa.String(36), primary_key=True),
        sa.Column("course_id",           sa.String(80), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language",            sa.String(20), nullable=False, server_default="tanglish"),
        sa.Column("speed",               sa.Float(),    nullable=False, server_default="1.0"),
        sa.Column("tone",                sa.String(40), nullable=False, server_default="friendly"),
        sa.Column("voice_id",            sa.String(80), nullable=False, server_default=""),
        sa.Column("custom_instructions", sa.Text(),     nullable=False, server_default=""),
        sa.Column("updated_at",          sa.DateTime(), nullable=True),
        sa.UniqueConstraint("course_id"),
    )

    # ── documents ─────────────────────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id",               sa.String(36),  primary_key=True),
        sa.Column("course_id",        sa.String(80),  nullable=False, index=True),
        sa.Column("original_filename",sa.String(260), nullable=False),
        sa.Column("storage_key",      sa.String(512), nullable=False, unique=True),
        sa.Column("mime_type",        sa.String(120), nullable=False),
        sa.Column("size_bytes",       sa.Integer(),   nullable=False),
        sa.Column("status",           sa.String(20),  nullable=False, server_default="uploaded"),
        sa.Column("error_message",    sa.Text(),      nullable=True),
        sa.Column("page_count",       sa.Integer(),   nullable=True),
        sa.Column("chunk_count",      sa.Integer(),   nullable=True),
        sa.Column("embedding_model",  sa.String(120), nullable=True),
        sa.Column("embedding_dim",    sa.Integer(),   nullable=True),
        sa.Column("uploaded_by",      sa.String(36),  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at",       sa.DateTime(),  nullable=True),
        sa.Column("updated_at",       sa.DateTime(),  nullable=True),
    )

    # ── document_chunks ───────────────────────────────────────────────────────
    op.create_table(
        "document_chunks",
        sa.Column("id",              sa.String(36), primary_key=True),
        sa.Column("document_id",     sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("chunk_index",     sa.Integer(),  nullable=False),
        sa.Column("text",            sa.Text(),     nullable=False),
        sa.Column("heading_path",    sa.JSON(),     nullable=False),
        sa.Column("page_number",     sa.Integer(),  nullable=False, server_default="1"),
        sa.Column("token_count",     sa.Integer(),  nullable=False, server_default="0"),
        sa.Column("embedding_model", sa.String(120),nullable=False),
        sa.Column("vector_id",       sa.String(36), nullable=False),
        sa.Column("created_at",      sa.DateTime(), nullable=True),
    )

    # ── media_assets ──────────────────────────────────────────────────────────
    op.create_table(
        "media_assets",
        sa.Column("id",          sa.String(36),  primary_key=True),
        sa.Column("page_id",     sa.String(80),  nullable=True),
        sa.Column("kind",        sa.String(20),  nullable=False),
        sa.Column("mime_type",   sa.String(120), nullable=False),
        sa.Column("url",         sa.Text(),      nullable=False),
        sa.Column("duration_ms", sa.Float(),     nullable=True),
        sa.Column("created_at",  sa.DateTime(),  nullable=True),
    )

    # ── jobs ──────────────────────────────────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column("id",          sa.String(36), primary_key=True),
        sa.Column("kind",        sa.String(40), nullable=False),
        sa.Column("status",      sa.String(20), nullable=False, server_default="queued"),
        sa.Column("course_id",   sa.String(80), nullable=True),
        sa.Column("error",       sa.Text(),     nullable=True),
        sa.Column("job_meta",    sa.JSON(),     nullable=False),
        sa.Column("created_at",  sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )

    # ── llm_usage ─────────────────────────────────────────────────────────────
    op.create_table(
        "llm_usage",
        sa.Column("id",         sa.String(36),  primary_key=True),
        sa.Column("date",       sa.String(10),  nullable=False, index=True),
        sa.Column("provider",   sa.String(40),  nullable=False),
        sa.Column("model",      sa.String(120), nullable=False),
        sa.Column("tokens_in",  sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(),  nullable=True),
    )

    # ── generated_pages ───────────────────────────────────────────────────────
    op.create_table(
        "generated_pages",
        sa.Column("id",             sa.String(36), primary_key=True),
        sa.Column("course_id",      sa.String(80), nullable=False, index=True),
        sa.Column("chapter_number", sa.Integer(),  nullable=False),
        sa.Column("page_number",    sa.Integer(),  nullable=False),
        sa.Column("cache_key",      sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("status",         sa.String(20), nullable=False, server_default="draft"),
        sa.Column("content",        sa.JSON(),     nullable=False),
        sa.Column("version",        sa.Integer(),  nullable=False, server_default="1"),
        sa.Column("created_at",     sa.DateTime(), nullable=True),
        sa.Column("updated_at",     sa.DateTime(), nullable=True),
    )

    # ── page_gen_logs ─────────────────────────────────────────────────────────
    op.create_table(
        "page_gen_logs",
        sa.Column("id",         sa.String(36), primary_key=True),
        sa.Column("cache_key",  sa.String(64), nullable=False, index=True),
        sa.Column("course_id",  sa.String(80), nullable=False),
        sa.Column("chapter",    sa.Integer(),  nullable=False),
        sa.Column("page",       sa.Integer(),  nullable=False),
        sa.Column("attempts",   sa.JSON(),     nullable=False),
        sa.Column("success",    sa.Boolean(),  nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    # ── bandit_arms ───────────────────────────────────────────────────────────
    op.create_table(
        "bandit_arms",
        sa.Column("id",         sa.String(36),  primary_key=True),
        sa.Column("user_id",    sa.String(36),  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("topic",      sa.String(120), nullable=False, index=True),
        sa.Column("arm",        sa.Integer(),   nullable=False),
        sa.Column("alpha",      sa.Float(),     nullable=False, server_default="1.0"),
        sa.Column("beta",       sa.Float(),     nullable=False, server_default="1.0"),
        sa.Column("updated_at", sa.DateTime(),  nullable=True),
        sa.UniqueConstraint("user_id", "topic", "arm"),
    )

    # ── fsrs_cards ────────────────────────────────────────────────────────────
    op.create_table(
        "fsrs_cards",
        sa.Column("id",               sa.String(36),  primary_key=True),
        sa.Column("user_id",          sa.String(36),  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("topic",            sa.String(120), nullable=False, index=True),
        sa.Column("stability",        sa.Float(),     nullable=False, server_default="1.0"),
        sa.Column("difficulty",       sa.Float(),     nullable=False, server_default="5.0"),
        sa.Column("last_review_date", sa.DateTime(),  nullable=True),
        sa.Column("review_count",     sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("lapses",           sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("updated_at",       sa.DateTime(),  nullable=True),
        sa.UniqueConstraint("user_id", "topic"),
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("fsrs_cards")
    op.drop_table("bandit_arms")
    op.drop_table("page_gen_logs")
    op.drop_table("generated_pages")
    op.drop_table("llm_usage")
    op.drop_table("jobs")
    op.drop_table("media_assets")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("tutor_settings")
    op.drop_table("history")
    op.drop_table("notifications")
    op.drop_table("sessions")
    op.drop_table("notes")
    op.drop_table("bookmarks")
    op.drop_table("progress")
    op.drop_table("pages")
    op.drop_table("chapters")
    op.drop_table("password_reset_tokens")
    op.drop_table("ws_tickets")
    op.drop_table("invites")
    op.drop_table("seats")
    op.drop_table("courses")
    op.drop_table("users")
