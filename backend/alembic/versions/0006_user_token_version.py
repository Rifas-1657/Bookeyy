"""users.token_version — JWT revocation on logout / password reset.

Revision ID: 0006_user_token_version
Revises: 0005_learner_books
Create Date: 2026-11-03 00:00:00.000000

ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0

Note on timestamps: every DateTime column (including invites / ws_tickets /
password_reset_tokens expiry columns) stores NAIVE UTC. The application now
writes and compares naive UTC consistently (app.db.models.utcnow), so no
column type change is needed.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_user_token_version"
down_revision = "0005_learner_books"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("token_version")
