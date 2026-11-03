"""Durable job queue on `jobs` + schema sync with the ORM models.

Revision ID: 0007_durable_jobs_schema_sync
Revises: 0006_user_token_version

1. `jobs` (unused until now) becomes the durable job queue (app/jobs):
   payload, dedupe_key, priority, attempts, max_attempts, run_after, locked_by,
   lease_expires_at, started_at, updated_at + claim / dedupe indexes
   (partial unique index: one queued/running job per dedupe_key).
2. Timestamp columns the models declare NOT NULL were created nullable by
   0001–0006: backfill NULLs with UTC now, then set NOT NULL.
3. learner_books.course_id had both a UNIQUE constraint and a unique index;
   the model only declares the index (PostgreSQL: drop the duplicate constraint).
4. PostgreSQL only: GIN full-text index for /search
   (to_tsvector('simple', title || ' ' || narration_text)).  SQLite keeps FTS5,
   which the app creates at startup.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_durable_jobs_schema_sync"
down_revision = "0006_user_token_version"
branch_labels = None
depends_on = None

# (table, column) pairs declared NOT NULL in app/db/models.py but nullable in 0001–0006.
NOT_NULL_TIMESTAMPS = [
    ("bandit_arms", "updated_at"),
    ("bookmarks", "created_at"),
    ("course_build_jobs", "created_at"),
    ("course_build_jobs", "updated_at"),
    ("course_build_lessons", "updated_at"),
    ("course_surveys", "completed_at"),
    ("course_surveys", "updated_at"),
    ("courses", "created_at"),
    ("document_chunks", "created_at"),
    ("documents", "created_at"),
    ("documents", "updated_at"),
    ("fsrs_cards", "updated_at"),
    ("generated_pages", "created_at"),
    ("generated_pages", "updated_at"),
    ("history", "visited_at"),
    ("invites", "created_at"),
    ("jobs", "created_at"),
    ("learner_books", "created_at"),
    ("llm_usage", "created_at"),
    ("media_assets", "created_at"),
    ("notes", "created_at"),
    ("notes", "updated_at"),
    ("notifications", "created_at"),
    ("page_gen_logs", "created_at"),
    ("pages", "created_at"),
    ("password_reset_tokens", "created_at"),
    ("progress", "updated_at"),
    ("seats", "granted_at"),
    ("sessions", "started_at"),
    ("source_jobs", "created_at"),
    ("tutor_settings", "updated_at"),
    ("users", "created_at"),
    ("users", "updated_at"),
    ("ws_tickets", "created_at"),
]

ACTIVE_WHERE = "status IN ('queued', 'running') AND dedupe_key IS NOT NULL"
PAGES_TSV = "to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(narration_text, ''))"


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _utc_now_sql() -> str:
    # Columns hold naive UTC; Postgres CURRENT_TIMESTAMP would be server-local time.
    return "(now() at time zone 'utc')" if _is_pg() else "CURRENT_TIMESTAMP"


def upgrade() -> None:
    now = _utc_now_sql()

    # 1. jobs → durable queue --------------------------------------------------
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("dedupe_key", sa.String(200), nullable=True))
        batch.add_column(sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"))
        batch.add_column(sa.Column("run_after", sa.DateTime(), nullable=False, server_default=sa.text(now)))
        batch.add_column(sa.Column("locked_by", sa.String(120), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("started_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text(now)))
    op.create_index("ix_jobs_claim", "jobs", ["status", "run_after"])
    op.create_index("ix_jobs_dedupe_key", "jobs", ["dedupe_key"])
    op.create_index("ux_jobs_active_dedupe", "jobs", ["dedupe_key"], unique=True,
                    postgresql_where=sa.text(ACTIVE_WHERE), sqlite_where=sa.text(ACTIVE_WHERE))

    # 2. NOT NULL timestamps -----------------------------------------------------
    by_table: dict[str, list[str]] = {}
    for table, col in NOT_NULL_TIMESTAMPS:
        by_table.setdefault(table, []).append(col)
    for table, cols in by_table.items():
        for col in cols:
            op.execute(f"UPDATE {table} SET {col} = {now} WHERE {col} IS NULL")
        with op.batch_alter_table(table) as batch:
            for col in cols:
                batch.alter_column(col, existing_type=sa.DateTime(), nullable=False)

    # 3 + 4. PostgreSQL-only cleanups ---------------------------------------------
    if _is_pg():
        insp = sa.inspect(op.get_bind())
        for uc in insp.get_unique_constraints("learner_books"):
            if uc.get("column_names") == ["course_id"] and uc.get("name"):
                op.drop_constraint(uc["name"], "learner_books", type_="unique")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_pages_fts ON pages USING gin ({PAGES_TSV})")


def downgrade() -> None:
    if _is_pg():
        op.execute("DROP INDEX IF EXISTS ix_pages_fts")
        op.create_unique_constraint("learner_books_course_id_key", "learner_books", ["course_id"])

    by_table: dict[str, list[str]] = {}
    for table, col in NOT_NULL_TIMESTAMPS:
        by_table.setdefault(table, []).append(col)
    for table, cols in by_table.items():
        with op.batch_alter_table(table) as batch:
            for col in cols:
                batch.alter_column(col, existing_type=sa.DateTime(), nullable=True)

    op.drop_index("ux_jobs_active_dedupe", table_name="jobs")
    op.drop_index("ix_jobs_dedupe_key", table_name="jobs")
    op.drop_index("ix_jobs_claim", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        for col in ("updated_at", "started_at", "lease_expires_at", "locked_by", "run_after",
                    "max_attempts", "attempts", "priority", "dedupe_key", "payload"):
            batch.drop_column(col)
