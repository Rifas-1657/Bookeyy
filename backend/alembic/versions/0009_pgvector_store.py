"""pgvector vector store (VECTOR_STORE=pgvector) — PostgreSQL only.

Revision ID: 0009_pgvector_store
Revises: 0008_learner_model

EXTENSION vector
NEW TABLE kb_vectors   one row per (course, embedding model, chunk): untyped `vector`
                       column + its dimension, chunk text and metadata
INDEX     ix_kb_vectors_course_model_doc   (course_id, embedding_model, document_id)
INDEX     ix_kb_vectors_hnsw_<dim>         partial HNSW (cosine) for the configured
                                           embedding backend's dimension; the store adds
                                           one on demand for any other dimension

SQLite (dev/tests) keeps using Chroma, so this revision does nothing there. kb_vectors is
not an ORM model (raw SQL in app/knowledge/vectorstore/pgvector.py); alembic/env.py and
tests/test_migrations.py exclude it from autogenerate comparisons.
"""
from __future__ import annotations

from alembic import op

revision = "0009_pgvector_store"
down_revision = "0008_learner_model"
branch_labels = None
depends_on = None


def _backend_dimension() -> int:
    try:
        from app.knowledge import get_embedding_backend
        return int(get_embedding_backend().dimension)
    except Exception:
        return 384


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
        CREATE TABLE IF NOT EXISTS kb_vectors (
            course_id       VARCHAR(80)  NOT NULL,
            embedding_model VARCHAR(200) NOT NULL,
            chunk_id        VARCHAR(80)  NOT NULL,
            document_id     VARCHAR(36)  NOT NULL,
            dimension       INTEGER      NOT NULL,
            embedding       vector       NOT NULL,
            text            TEXT         NOT NULL DEFAULT '',
            page_number     INTEGER      NOT NULL DEFAULT 0,
            heading_path    TEXT         NOT NULL DEFAULT '[]',
            token_count     INTEGER      NOT NULL DEFAULT 0,
            created_at      TIMESTAMP    NOT NULL DEFAULT now(),
            PRIMARY KEY (course_id, embedding_model, chunk_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_kb_vectors_course_model_doc "
               "ON kb_vectors (course_id, embedding_model, document_id)")
    from app.knowledge.vectorstore.pgvector import hnsw_index_sql
    dim = _backend_dimension()
    if dim <= 2000:   # pgvector HNSW limit for `vector`
        op.execute(hnsw_index_sql(dim))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS kb_vectors")
