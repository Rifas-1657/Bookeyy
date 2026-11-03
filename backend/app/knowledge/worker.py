"""
app/knowledge/worker.py  —  Phase-1 extended document processing pipeline.

Public entry-points
───────────────────
process_document(document_id)
    Full pipeline for one already-stored document (file or URL source).
    Called by the admin upload endpoint as a BackgroundTask.
    Also called by the retry endpoint.

process_job(job_id)
    Drives a multi-item SourceJob: iterates over every Document row that
    belongs to the job, calls process_document() for each, updates
    SourceJob.progress_pct / done_items / failed_items, and sets the final
    job status (done | partial | failed) when all items finish.
    Called by the multi-upload and URL-batch endpoints as a BackgroundTask.

Duplicate detection
───────────────────
Before indexing, each document's raw bytes are SHA-256 hashed and compared
against existing documents in the same course.  A duplicate is marked
status="duplicate" with an informative error_message; it is NOT re-indexed.
The first document that was indexed retains its chunks intact.

Pipeline per document
─────────────────────
  1.  Load raw bytes (from storage for file sources; fetch via URL for URL
      sources whose storage_key is the sentinel "url:<url>").
  2.  Compute + persist content_hash; check for intra-course duplicates.
  3.  If URL source: store fetched bytes under a random storage key.
  4.  Parse (PDF with optional OCR / DOCX / MD / TXT).
  5.  Chunk into token-bounded segments.
  6.  Delete old chunks + vectors (idempotent retry support).
  7.  Embed (batched) + upsert into vector store.
  8.  Persist DocumentChunk rows and update Document final state.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal
from app.db.models import Document, DocumentChunk, SourceJob
from app.knowledge import get_embedding_backend, get_storage, get_vector_store
from app.knowledge.chunking import chunk_pages
from app.knowledge.parsing import ParseError, parse_document

log = logging.getLogger(__name__)

# Sentinel prefix: URL-sourced docs use this as their storage_key until fetched
_URL_KEY_PREFIX = "url:"


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _set_doc_status(
    doc_id: str,
    status: str,
    error: str | None = None,
    *,
    page_count: int | None = None,
    chunk_count: int | None = None,
    embedding_model: str | None = None,
    embedding_dim: int | None = None,
    content_hash: str | None = None,
    storage_key: str | None = None,
) -> None:
    """Persist document status + optional fields in one DB write."""
    async with AsyncSessionLocal() as db:
        doc = await db.get(Document, doc_id)
        if doc is None:
            return
        doc.status = status
        if error is not None:
            doc.error_message = error[:2000]
        if page_count is not None:
            doc.page_count = page_count
        if chunk_count is not None:
            doc.chunk_count = chunk_count
        if embedding_model is not None:
            doc.embedding_model = embedding_model
        if embedding_dim is not None:
            doc.embedding_dim = embedding_dim
        if content_hash is not None:
            doc.content_hash = content_hash
        if storage_key is not None:
            doc.storage_key = storage_key
        await db.commit()


async def _find_duplicate(
    db: AsyncSession,
    course_id: str,
    content_hash: str,
    exclude_doc_id: str,
) -> Document | None:
    """Return an existing indexed/processing document with the same hash, or None."""
    result = await db.execute(
        select(Document).where(
            Document.course_id == course_id,
            Document.content_hash == content_hash,
            Document.status.in_(["indexed", "processing", "uploaded"]),
            Document.id != exclude_doc_id,
        )
    )
    return result.scalar_one_or_none()


async def _update_job_progress(job_id: str | None) -> None:
    """Recompute and persist SourceJob progress from its child documents."""
    if job_id is None:
        return
    async with AsyncSessionLocal() as db:
        job = await db.get(SourceJob, job_id)
        if job is None:
            return
        result = await db.execute(
            select(Document).where(Document.source_job_id == job_id)
        )
        docs = result.scalars().all()
        done = sum(1 for d in docs if d.status == "indexed")
        failed = sum(1 for d in docs if d.status in ("failed", "duplicate"))
        total = len(docs)
        job.done_items = done
        job.failed_items = failed
        job.progress_pct = int((done + failed) / total * 100) if total else 0
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# process_document — single-document full pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def process_document(document_id: str) -> None:
    """
    Full ingestion pipeline for one document.
    Idempotent: safe to call multiple times (re-processes from scratch).
    Updates parent SourceJob progress if one exists.
    """
    log.info("worker: processing document=%s", document_id)
    await _set_doc_status(document_id, "processing")

    storage = get_storage()
    emb = get_embedding_backend()
    vs = get_vector_store()

    try:
        # ----------------------------------------------------------------
        # 1. Load document metadata
        # ----------------------------------------------------------------
        async with AsyncSessionLocal() as db:
            doc: Document | None = await db.get(Document, doc_id := document_id)
            if doc is None:
                log.error("Document %s not found in DB", document_id)
                return
            storage_key = doc.storage_key
            mime_type = doc.mime_type
            course_id = doc.course_id
            source_type = doc.source_type
            source_url = doc.source_url
            job_id = doc.source_job_id

        # ----------------------------------------------------------------
        # 2. Obtain raw bytes
        #    • file source  → retrieve from storage
        #    • URL source   → fetch from network, then persist to storage
        # ----------------------------------------------------------------
        if source_type == "url" and storage_key.startswith(_URL_KEY_PREFIX):
            # URL not yet fetched — do it now
            from app.knowledge.url_fetcher import FetchError, fetch_url
            if not source_url:
                raise ValueError("URL source document has no source_url set.")
            try:
                result = await fetch_url(source_url)
            except FetchError as exc:
                raise ParseError(f"URL fetch failed: {exc}") from exc

            raw_bytes = result.data
            # Persist to storage under a stable random key
            rand_prefix = secrets.token_hex(16)
            ext = result.filename.rsplit(".", 1)[-1].lower() if "." in result.filename else "bin"
            real_storage_key = f"documents/{course_id}/{rand_prefix}.{ext}"
            await storage.store(real_storage_key, raw_bytes)

            # Update the document record with the real storage key and actual MIME
            mime_type = result.mime_type
            await _set_doc_status(
                document_id,
                "processing",
                storage_key=real_storage_key,
            )
            # Also update mime_type in DB
            async with AsyncSessionLocal() as db:
                doc2 = await db.get(Document, document_id)
                if doc2:
                    doc2.mime_type = mime_type
                    await db.commit()
            storage_key = real_storage_key
        else:
            raw_bytes = await storage.retrieve(storage_key)

        # ----------------------------------------------------------------
        # 3. Compute content hash + duplicate detection
        # ----------------------------------------------------------------
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        async with AsyncSessionLocal() as db:
            duplicate = await _find_duplicate(db, course_id, content_hash, document_id)

        if duplicate is not None:
            log.info(
                "document=%s is a duplicate of document=%s (hash=%s)",
                document_id, duplicate.id, content_hash[:16],
            )
            await _set_doc_status(
                document_id,
                "duplicate",
                error=(
                    f"This file is identical to '{duplicate.original_filename}' "
                    f"(id {duplicate.id}) which is already indexed."
                ),
                content_hash=content_hash,
            )
            await _update_job_progress(job_id)
            return

        # Persist the hash
        await _set_doc_status(document_id, "processing", content_hash=content_hash)

        # ----------------------------------------------------------------
        # 4. Parse
        # ----------------------------------------------------------------
        from app.config import get_settings
        ocr_enabled = get_settings().ocr_enabled
        pages = await parse_document(raw_bytes, mime_type, ocr_enabled=ocr_enabled)
        page_count = len(pages)
        log.info("document=%s parsed: pages=%d", document_id, page_count)

        # ----------------------------------------------------------------
        # 5. Chunk
        # ----------------------------------------------------------------
        chunks = chunk_pages(pages)
        log.info("document=%s chunks=%d", document_id, len(chunks))

        # ----------------------------------------------------------------
        # 6. Delete old chunks + vectors (retry safety)
        # ----------------------------------------------------------------
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
            await db.commit()
        await vs.delete_by_document(course_id, emb.name, document_id)

        # ----------------------------------------------------------------
        # 7. Embed + upsert (batched)
        # ----------------------------------------------------------------
        batch_size = 32
        db_chunks: list[DocumentChunk] = []

        for batch_start in range(0, len(chunks), batch_size):
            batch = chunks[batch_start : batch_start + batch_size]
            texts = [c.text for c in batch]
            vectors = await emb.embed_batch(texts)

            for chunk, vector in zip(batch, vectors):
                chunk_id = str(uuid.uuid4())
                await vs.upsert(
                    course_id=course_id,
                    embedding_model=emb.name,
                    dimension=emb.dimension,
                    chunk_id=chunk_id,
                    document_id=document_id,
                    vector=vector,
                    text=chunk.text,
                    page_number=chunk.page_number,
                    heading_path=chunk.heading_path,
                    token_count=chunk.token_count,
                )
                db_chunks.append(
                    DocumentChunk(
                        id=chunk_id,
                        document_id=document_id,
                        chunk_index=chunk.chunk_index,
                        text=chunk.text,
                        heading_path=chunk.heading_path,
                        page_number=chunk.page_number,
                        token_count=chunk.token_count,
                        embedding_model=emb.name,
                        vector_id=chunk_id,
                    )
                )

        # ----------------------------------------------------------------
        # 8. Persist chunks + finalise document
        # ----------------------------------------------------------------
        async with AsyncSessionLocal() as db:
            db.add_all(db_chunks)
            doc3 = await db.get(Document, document_id)
            if doc3:
                doc3.status = "indexed"
                doc3.page_count = page_count
                doc3.chunk_count = len(db_chunks)
                doc3.embedding_model = emb.name
                doc3.embedding_dim = emb.dimension
                doc3.error_message = None
                doc3.content_hash = content_hash
            await db.commit()

        log.info(
            "document=%s indexed: pages=%d chunks=%d model=%s",
            document_id, page_count, len(db_chunks), emb.name,
        )

    except ParseError as exc:
        log.warning("document=%s parse error: %s", document_id, exc)
        await _set_doc_status(document_id, "failed", str(exc))

    except Exception:
        log.exception("document=%s unexpected error", document_id)
        await _set_doc_status(
            document_id, "failed", "Internal processing error. Check server logs."
        )

    finally:
        # Always update parent job progress after each document finishes
        async with AsyncSessionLocal() as db:
            doc_final = await db.get(Document, document_id)
            job_id_final = doc_final.source_job_id if doc_final else None
        await _update_job_progress(job_id_final)


# ─────────────────────────────────────────────────────────────────────────────
# process_job — drives an entire SourceJob batch
# ─────────────────────────────────────────────────────────────────────────────

async def process_job(job_id: str) -> None:
    """
    Drive a multi-item SourceJob batch.
    Processes all documents belonging to the job sequentially (to avoid
    overwhelming the embedding backend on single-worker deployments).
    Updates job status to 'done', 'partial', or 'failed' when finished.
    """
    log.info("worker: processing job=%s", job_id)

    # Mark job as processing
    async with AsyncSessionLocal() as db:
        job = await db.get(SourceJob, job_id)
        if job is None:
            log.error("SourceJob %s not found", job_id)
            return
        job.status = "processing"
        await db.commit()

        # Collect document IDs upfront
        result = await db.execute(
            select(Document.id).where(Document.source_job_id == job_id)
        )
        doc_ids: list[str] = [row[0] for row in result.fetchall()]

    log.info("job=%s: processing %d documents", job_id, len(doc_ids))

    for doc_id in doc_ids:
        try:
            await process_document(doc_id)
        except Exception:
            log.exception("job=%s: uncaught error processing document=%s", job_id, doc_id)
            # process_document has its own exception handler; this is belt-and-suspenders

    # Determine final job status
    async with AsyncSessionLocal() as db:
        job = await db.get(SourceJob, job_id)
        if job is None:
            return
        result = await db.execute(
            select(Document).where(Document.source_job_id == job_id)
        )
        docs = result.scalars().all()
        done = sum(1 for d in docs if d.status == "indexed")
        failed = sum(1 for d in docs if d.status in ("failed", "duplicate"))
        total = len(docs)

        job.done_items = done
        job.failed_items = failed
        job.progress_pct = 100
        job.finished_at = datetime.utcnow()

        if failed == 0:
            job.status = "done"
        elif done == 0:
            job.status = "failed"
        else:
            job.status = "partial"

        if failed > 0:
            # Summarise failures for the admin
            failed_names = [
                d.original_filename
                for d in docs
                if d.status in ("failed", "duplicate")
            ]
            job.error_summary = (
                f"{failed}/{total} item(s) failed or were duplicates: "
                + ", ".join(failed_names[:10])
                + ("…" if len(failed_names) > 10 else "")
            )
        await db.commit()

    log.info(
        "job=%s finished: status=%s done=%d failed=%d total=%d",
        job_id, job.status, done, failed, total,
    )
