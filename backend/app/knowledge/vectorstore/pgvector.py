"""
app/knowledge/vectorstore/pgvector.py

PostgreSQL + pgvector vector store (production; VECTOR_STORE=pgvector).

One table `kb_vectors` (migration 0009) holds every course's chunks. The `embedding`
column is an untyped `vector` so several embedding models (dimensions) can coexist; each
row records its model and dimension, queries filter on both, and a partial HNSW index per
dimension (`ix_kb_vectors_hnsw_<dim>`, created by the migration for the configured backend
and on demand here) keeps search fast:

    ... WHERE dimension = 384 ORDER BY (embedding::vector(384)) <=> :q

Vectors travel as text literals ('[0.1,0.2,...]') cast to `vector`, so neither asyncpg
codecs nor the `pgvector` Python package are needed. Works through PgBouncer in
transaction mode (no session state, no prepared statements beyond the driver's own).
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import bindparam
from sqlalchemy import text as sql_text

from app.knowledge.vectorstore import VectorMatch, VectorStore

log = logging.getLogger(__name__)

TABLE = "kb_vectors"


def _vec_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.7g}" for x in vector) + "]"


def hnsw_index_sql(dimension: int) -> str:
    d = int(dimension)
    return (f"CREATE INDEX IF NOT EXISTS ix_kb_vectors_hnsw_{d} ON {TABLE} "
            f"USING hnsw ((embedding::vector({d})) vector_cosine_ops) WHERE dimension = {d}")


class PgVectorStore(VectorStore):
    def __init__(self, engine=None) -> None:
        self._engine = engine
        self._indexed: set[int] = set()

    def _get_engine(self):
        if self._engine is None:
            from app.db.database import engine
            self._engine = engine
        return self._engine

    async def _ensure_index(self, conn, dimension: int) -> None:
        if dimension in self._indexed:
            return
        # A savepoint keeps a failed CREATE INDEX (e.g. > 2000 dims) from aborting the upsert;
        # the index is only an optimisation (exact search still works without it).
        try:
            async with conn.begin_nested():
                await conn.execute(sql_text(hnsw_index_sql(dimension)))
        except Exception:  # pragma: no cover
            log.warning("could not create HNSW index for dimension %s", dimension, exc_info=True)
        self._indexed.add(dimension)

    async def upsert(
        self,
        course_id: str,
        embedding_model: str,
        dimension: int,
        chunk_id: str,
        document_id: str,
        vector: list[float],
        text: str,
        page_number: int,
        heading_path: list[str],
        token_count: int,
    ) -> None:
        if len(vector) != dimension:
            raise RuntimeError(f"Vector has {len(vector)} dims, expected {dimension}")
        sql = f"""
            INSERT INTO {TABLE} (course_id, embedding_model, chunk_id, document_id, dimension,
                                 embedding, text, page_number, heading_path, token_count)
            VALUES (:course_id, :model, :chunk_id, :document_id, :dimension,
                    CAST(CAST(:vec AS text) AS vector), :text, :page_number, :heading_path, :token_count)
            ON CONFLICT (course_id, embedding_model, chunk_id) DO UPDATE SET
                document_id = EXCLUDED.document_id, dimension = EXCLUDED.dimension,
                embedding = EXCLUDED.embedding, text = EXCLUDED.text,
                page_number = EXCLUDED.page_number, heading_path = EXCLUDED.heading_path,
                token_count = EXCLUDED.token_count
        """
        async with self._get_engine().begin() as conn:
            if dimension not in self._indexed:
                await self._ensure_index(conn, dimension)
            await conn.execute(sql_text(sql), {
                "course_id": course_id, "model": embedding_model, "chunk_id": chunk_id,
                "document_id": document_id, "dimension": int(dimension),
                "vec": _vec_literal(vector), "text": text, "page_number": int(page_number or 0),
                "heading_path": json.dumps(heading_path or []), "token_count": int(token_count or 0),
            })

    async def query(
        self,
        course_id: str,
        embedding_model: str,
        dimension: int,
        query_vector: list[float],
        top_k: int = 10,
        document_ids: list[str] | None = None,
    ) -> list[VectorMatch]:
        if document_ids is not None and not document_ids:
            return []
        d = int(dimension)
        where = "course_id = :course_id AND embedding_model = :model AND dimension = :dimension"
        params: dict = {"course_id": course_id, "model": embedding_model, "dimension": d,
                        "q": _vec_literal(query_vector), "k": int(top_k)}
        if document_ids is not None:
            where += " AND document_id IN :doc_ids"
            params["doc_ids"] = list(document_ids)
        stmt = sql_text(f"""
            SELECT chunk_id, document_id, text, page_number, heading_path, token_count,
                   (embedding::vector({d})) <=> CAST(CAST(:q AS text) AS vector({d})) AS distance
            FROM {TABLE}
            WHERE {where}
            ORDER BY distance
            LIMIT :k
        """)
        if document_ids is not None:
            stmt = stmt.bindparams(bindparam("doc_ids", expanding=True))
        async with self._get_engine().connect() as conn:
            rows = (await conn.execute(stmt, params)).mappings().all()
        out: list[VectorMatch] = []
        for r in rows:
            try:
                heading = json.loads(r["heading_path"] or "[]")
            except (TypeError, ValueError):
                heading = []
            out.append(VectorMatch(
                chunk_id=r["chunk_id"], document_id=r["document_id"], text=r["text"] or "",
                score=1.0 - float(r["distance"]), page_number=int(r["page_number"] or 0),
                heading_path=heading, token_count=int(r["token_count"] or 0),
            ))
        return out

    async def delete_by_document(self, course_id: str, embedding_model: str, document_id: str) -> None:
        async with self._get_engine().begin() as conn:
            await conn.execute(sql_text(
                f"DELETE FROM {TABLE} WHERE course_id = :c AND embedding_model = :m AND document_id = :d"),
                {"c": course_id, "m": embedding_model, "d": document_id})

    async def collection_info(self, course_id: str, embedding_model: str) -> dict:
        async with self._get_engine().connect() as conn:
            row = (await conn.execute(sql_text(
                f"SELECT COUNT(*) AS n, MIN(dimension) AS dim FROM {TABLE} "
                "WHERE course_id = :c AND embedding_model = :m"),
                {"c": course_id, "m": embedding_model})).mappings().one()
        return {"name": f"{TABLE}:{course_id}:{embedding_model}", "count": int(row["n"]),
                "metadata": {"dimension": row["dim"], "embedding_model": embedding_model,
                             "course_id": course_id}}
