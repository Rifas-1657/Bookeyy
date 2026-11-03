"""Course builder jobs + lessons.

Revision ID: 0004_course_builder
Revises: 0003_course_surveys
Create Date: 2026-10-31 00:00:00.000000

NEW TABLE course_build_jobs     one row per admin multi-source course build
NEW TABLE course_build_lessons  generated page per outline lesson
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_course_builder"
down_revision = "0003_course_surveys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_build_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("course_id", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("outline", sa.JSON(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_by", sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_course_build_jobs_course_id", "course_build_jobs", ["course_id"])

    op.create_table(
        "course_build_lessons",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id", sa.String(36),
            sa.ForeignKey("course_build_jobs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("lesson_key", sa.String(40), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("lesson_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("page", sa.JSON(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("job_id", "lesson_key"),
    )
    op.create_index("ix_course_build_lessons_job_id", "course_build_lessons", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_course_build_lessons_job_id", table_name="course_build_lessons")
    op.drop_table("course_build_lessons")
    op.drop_index("ix_course_build_jobs_course_id", table_name="course_build_jobs")
    op.drop_table("course_build_jobs")
