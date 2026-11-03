"""
app/knowledge/sources/ingest.py — store SourceDocuments and index them with the
EXISTING worker pipeline (parse → chunk → embed → vector store → DocumentChunk rows).

Each SourceDocument becomes one `Document` row (mime text/markdown,
source_type = the adapter type, source_url = original URL) whose bytes live in
the configured storage backend.  `process_document()` then indexes it exactly
like an uploaded Markdown file, so retrieval / tutor / page generation all work
unchanged.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.db.models import Document
from app.knowledge import get_storage
from app.knowledge.sources.base import SourceDocument
from app.knowledge.worker import process_document

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    document_id: str
    title: str
    status: str            # indexed | failed | duplicate
    chunk_count: int
    error: str | None = None


def _safe_name(title: str) -> str:
    base = re.sub(r"[^\w\- .]+", "", title).strip()[:180] or "source"
    return base + ".md"


async def ingest_source_document(
    doc: SourceDocument,
    *,
    course_id: str,
    user_id: str,
    source_job_id: str | None = None,
) -> IngestResult:
    data = doc.text.encode("utf-8")
    content_hash = hashlib.sha256(data).hexdigest()

    # Already indexed in this course? Re-use it instead of failing as duplicate.
    async with AsyncSessionLocal() as db:
        existing = (await db.execute(
            select(Document).where(
                Document.course_id == course_id,
                Document.content_hash == content_hash,
                Document.status == "indexed",
            )
        )).scalars().first()
        if existing is not None:
            return IngestResult(existing.id, doc.title, "indexed", existing.chunk_count or 0)

    storage_key = f"documents/{course_id}/{secrets.token_hex(16)}.md"
    await get_storage().store(storage_key, data)
    doc_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        db.add(Document(
            id=doc_id,
            course_id=course_id,
            original_filename=_safe_name(doc.title),
            storage_key=storage_key,
            mime_type="text/markdown",
            size_bytes=len(data),
            status="uploaded",
            uploaded_by=user_id,
            source_type=(doc.kind or "url")[:10],
            source_url=doc.url or None,
            source_job_id=source_job_id,
        ))
        await db.commit()

    await process_document(doc_id)
    return await _result_for(doc_id, course_id, doc.title)


async def _result_for(doc_id: str, course_id: str, title: str) -> IngestResult:
    async with AsyncSessionLocal() as db:
        row = await db.get(Document, doc_id)
        if row is None:
            return IngestResult(doc_id, title, "failed", 0, "Document disappeared during indexing.")
        if row.status == "duplicate" and row.content_hash:
            orig = (await db.execute(select(Document).where(
                Document.course_id == course_id, Document.content_hash == row.content_hash,
                Document.id != doc_id, Document.status.in_(["indexed", "processing", "uploaded"]),
            ))).scalars().first()
            if orig:
                return IngestResult(orig.id, title, "indexed", orig.chunk_count or 0)
        return IngestResult(
            document_id=doc_id,
            title=title,
            status=row.status,
            chunk_count=row.chunk_count or 0,
            error=row.error_message if row.status != "indexed" else None,
        )


async def clone_document_into_course(
    document_id: str, *, course_id: str, user_id: str
) -> IngestResult:
    """Copy an already-uploaded Document (any course) into `course_id` and index it there."""
    async with AsyncSessionLocal() as db:
        src = await db.get(Document, document_id)
        if src is None:
            raise ValueError(f"Document '{document_id}' not found.")
        if src.course_id == course_id and src.status == "indexed":
            return IngestResult(src.id, src.original_filename, "indexed", src.chunk_count or 0)
        in_place = src.course_id == course_id and src.status == "uploaded" and not src.storage_key.startswith("url:")
        fname_in_place = src.original_filename
    if in_place:
        # Uploaded straight into this course (learner "Create a book") - index it where it is.
        await process_document(document_id)
        return await _result_for(document_id, course_id, fname_in_place)
    async with AsyncSessionLocal() as db:
        src = await db.get(Document, document_id)
        if src.storage_key.startswith("url:"):
            raise ValueError("Source document has not been fetched yet.")
        storage_key, mime, fname, stype, surl = (
            src.storage_key, src.mime_type, src.original_filename, src.source_type, src.source_url,
        )
    storage = get_storage()
    data = await storage.retrieve(storage_key)
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "bin"
    new_key = f"documents/{course_id}/{secrets.token_hex(16)}.{ext}"
    await storage.store(new_key, data)
    new_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        db.add(Document(
            id=new_id, course_id=course_id, original_filename=fname, storage_key=new_key,
            mime_type=mime, size_bytes=len(data), status="uploaded", uploaded_by=user_id,
            source_type=stype, source_url=surl,
        ))
        await db.commit()
    await process_document(new_id)
    return await _result_for(new_id, course_id, fname)
