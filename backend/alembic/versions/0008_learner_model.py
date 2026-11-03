"""R3 adaptive learning: per-user learner model + evidence log.

Revision ID: 0008_learner_model
Revises: 0007_durable_jobs_schema_sync

NEW TABLE learner_concepts   one row per (user, course, concept): BKT mastery P(L), counts
NEW TABLE learner_events     evidence log (quiz answers, questions, "simpler", code runs,
                             issued quiz items, teaching style shown)

The contextual teaching-style bandit reuses `bandit_arms` (topic "ts:<context>"), and spaced
review reuses `fsrs_cards` (topic "<course_id>::<concept_id>"), so neither needs a change.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_learner_model"
down_revision = "0007_durable_jobs_schema_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learner_concepts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.String(80), nullable=False),
        sa.Column("concept_id", sa.String(120), nullable=False),
        sa.Column("label", sa.String(200), nullable=False, server_default=""),
        sa.Column("page_id", sa.String(80), nullable=True),
        sa.Column("chapter_number", sa.Integer(), nullable=True),
        sa.Column("mastery", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confusion", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "course_id", "concept_id"),
    )
    op.create_index("ix_learner_concepts_user_id", "learner_concepts", ["user_id"])
    op.create_index("ix_learner_concepts_course_id", "learner_concepts", ["course_id"])

    op.create_table(
        "learner_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("page_id", sa.String(80), nullable=True),
        sa.Column("concept_id", sa.String(120), nullable=True),
        sa.Column("correct", sa.Boolean(), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_learner_events_user_course_created", "learner_events",
                    ["user_id", "course_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_learner_events_user_course_created", table_name="learner_events")
    op.drop_table("learner_events")
    op.drop_index("ix_learner_concepts_course_id", table_name="learner_concepts")
    op.drop_index("ix_learner_concepts_user_id", table_name="learner_concepts")
    op.drop_table("learner_concepts")
