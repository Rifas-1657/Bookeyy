"""Course survey answers.

Revision ID: 0003_course_surveys
Revises: 0002_phase1_source_ingestion
Create Date: 2026-10-27 00:00:00.000000

NEW TABLE course_surveys
    One row per (user, course) holding the learner's personalization answers.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_course_surveys"
down_revision = "0002_phase1_source_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_surveys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("course_id", sa.String(80), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "course_id"),
    )
    op.create_index("ix_course_surveys_user_id", "course_surveys", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_course_surveys_user_id", table_name="course_surveys")
    op.drop_table("course_surveys")
