"""users.email_verified_at — set when the learner confirms their email.

Revision ID: 0010_user_email_verified
Revises: 0009_pgvector_store
Create Date: 2026-11-08 00:00:00.000000

ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMP NULL
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_user_email_verified"
down_revision = "0009_pgvector_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("email_verified_at")
