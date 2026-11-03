"""Learner-generated books (ownership + visibility).

Revision ID: 0005_learner_books
Revises: 0004_course_builder
Create Date: 2026-11-03 00:00:00.000000

NEW TABLE learner_books   one row per learner "Create a book" job / private course
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_learner_books"
down_revision = "0004_course_builder"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learner_books",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("course_id", sa.String(80), nullable=False, unique=True),
        sa.Column("job_id", sa.String(36),
                  sa.ForeignKey("course_build_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.String(36),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False, server_default="upload"),
        sa.Column("source_label", sa.String(300), nullable=False, server_default=""),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="private"),
        sa.Column("ai_knowledge", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_learner_books_course_id", "learner_books", ["course_id"], unique=True)
    op.create_index("ix_learner_books_job_id", "learner_books", ["job_id"])
    op.create_index("ix_learner_books_owner_id", "learner_books", ["owner_id"])
    op.create_index("ix_learner_books_created_at", "learner_books", ["created_at"])


def downgrade() -> None:
    for ix in ("created_at", "owner_id", "job_id", "course_id"):
        op.drop_index(f"ix_learner_books_{ix}", table_name="learner_books")
    op.drop_table("learner_books")
