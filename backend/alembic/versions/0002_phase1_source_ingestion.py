"""Phase-1 source ingestion schema changes.

Revision ID: 0002_phase1_source_ingestion
Revises: 0001_baseline
Create Date: 2026-10-21 00:01:00.000000

Changes
───────
NEW TABLE source_jobs
    Persistent job record for a multi-file / URL ingestion batch.

NEW COLUMNS on documents
    content_hash  VARCHAR(64)  nullable, indexed
        SHA-256 hex of raw bytes — used for intra-course duplicate detection.
    source_type   VARCHAR(10)  not-null, default 'file'
        'file' for multipart uploads, 'url' for URL-sourced documents.
    source_url    TEXT         nullable
        Original URL for URL-sourced documents.
    source_job_id VARCHAR(36)  nullable, indexed, FK → source_jobs.id
        Parent batch job; NULL for standalone single-file uploads.

SQLite compatibility
────────────────────
SQLite does not support adding columns with FK constraints via plain
ALTER TABLE. We use batch_alter_table with recreate='auto'.  The FK on
source_job_id is declared with an explicit constraint name so that
SQLite batch mode can handle it correctly.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_phase1_source_ingestion"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── source_jobs (new table) ───────────────────────────────────────────────
    op.create_table(
        "source_jobs",
        sa.Column("id",            sa.String(36),  primary_key=True),
        sa.Column("course_id",     sa.String(80),  nullable=False),
        sa.Column("kind",          sa.String(20),  nullable=False),
        sa.Column("status",        sa.String(20),  nullable=False, server_default="queued"),
        sa.Column("total_items",   sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("done_items",    sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("failed_items",  sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("progress_pct",  sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text(),      nullable=True),
        sa.Column(
            "submitted_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_source_jobs_submitted_by"),
            nullable=False,
        ),
        sa.Column("created_at",    sa.DateTime(),  nullable=True),
        sa.Column("finished_at",   sa.DateTime(),  nullable=True),
    )
    op.create_index("ix_source_jobs_course_id", "source_jobs", ["course_id"])

    # ── documents: new columns ────────────────────────────────────────────────
    # batch_alter_table recreates the SQLite table when needed.
    # The source_job_id FK is expressed as a ForeignKeyConstraint with an
    # explicit name so the batch engine can manage it.
    with op.batch_alter_table(
        "documents",
        recreate="auto",
        table_kwargs={"extend_existing": True},
    ) as batch_op:
        batch_op.add_column(
            sa.Column("content_hash", sa.String(64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("source_type",  sa.String(10), nullable=False, server_default="file")
        )
        batch_op.add_column(
            sa.Column("source_url",   sa.Text(),     nullable=True)
        )
        batch_op.add_column(
            sa.Column("source_job_id", sa.String(36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_documents_source_job_id",   # explicit constraint name
            "source_jobs",
            ["source_job_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Indexes (create after batch_alter_table so they land on the new table)
    op.create_index("ix_documents_content_hash",  "documents", ["content_hash"])
    op.create_index("ix_documents_source_job_id", "documents", ["source_job_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_source_job_id", table_name="documents")
    op.drop_index("ix_documents_content_hash",  table_name="documents")

    with op.batch_alter_table("documents", recreate="auto") as batch_op:
        batch_op.drop_constraint("fk_documents_source_job_id", type_="foreignkey")
        batch_op.drop_column("source_job_id")
        batch_op.drop_column("source_url")
        batch_op.drop_column("source_type")
        batch_op.drop_column("content_hash")

    op.drop_index("ix_source_jobs_course_id", table_name="source_jobs")
    op.drop_table("source_jobs")
