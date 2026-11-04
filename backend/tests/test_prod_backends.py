"""Production backends: Supabase Storage (REST), pgvector store, fastembed embeddings."""
from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest

from app.knowledge.storage.supabase import SupabaseStorageBackend


def _fake_supabase():
    objects: dict[str, bytes] = {}
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        path = req.url.path
        assert req.headers["authorization"] == "Bearer svc" and req.headers["apikey"] == "svc"
        if req.method == "POST" and path.startswith("/storage/v1/object/bookey/"):
            assert req.headers["x-upsert"] == "true"
            objects[path.removeprefix("/storage/v1/object/bookey/")] = req.content
            return httpx.Response(200, json={"Key": path})
        if req.method == "GET" and path.startswith("/storage/v1/object/info/bookey/"):
            key = path.removeprefix("/storage/v1/object/info/bookey/")
            return httpx.Response(200 if key in objects else 400, json={})
        if req.method == "GET" and path.startswith("/storage/v1/object/bookey/"):
            key = path.removeprefix("/storage/v1/object/bookey/")
            if key not in objects:
                return httpx.Response(400, json={"statusCode": "404", "error": "not_found"})
            return httpx.Response(200, content=objects[key])
        if req.method == "DELETE" and path == "/storage/v1/object/bookey":
            for k in json.loads(req.content)["prefixes"]:
                objects.pop(k, None)
            return httpx.Response(200, json=[])
        return httpx.Response(500)

    return objects, seen, httpx.MockTransport(handler)


async def test_supabase_storage_roundtrip():
    objects, seen, transport = _fake_supabase()
    st = SupabaseStorageBackend("https://proj.supabase.co/", "svc", "bookey", transport=transport)
    await st.store("documents/c1/a.pdf", b"%PDF-1")
    assert objects == {"documents/c1/a.pdf": b"%PDF-1"}
    assert seen[0].headers["content-type"] == "application/pdf"
    assert await st.exists("documents/c1/a.pdf")
    assert await st.retrieve("documents/c1/a.pdf") == b"%PDF-1"
    assert await st.local_path("documents/c1/a.pdf") is None
    await st.delete("documents/c1/a.pdf")
    assert not await st.exists("documents/c1/a.pdf")
    with pytest.raises(FileNotFoundError):
        await st.retrieve("documents/c1/a.pdf")


async def test_supabase_storage_blocks_traversal():
    objects, _seen, transport = _fake_supabase()
    st = SupabaseStorageBackend("https://proj.supabase.co", "svc", "bookey", transport=transport)
    await st.store("/../x/../y.txt", b"1")
    assert list(objects) == ["__/x/__/y.txt"]


def test_supabase_storage_requires_config():
    with pytest.raises(RuntimeError):
        SupabaseStorageBackend("", "", "bookey")


def test_factory_selects_production_backends(monkeypatch):
    from app import knowledge
    from app.config import get_settings
    s = get_settings()
    for fn in (knowledge.get_storage, knowledge.get_vector_store):
        fn.cache_clear()
    try:
        monkeypatch.setattr(s, "storage_backend", "supabase")
        monkeypatch.setattr(s, "supabase_url", "https://proj.supabase.co")
        monkeypatch.setattr(s, "supabase_service_role_key", "svc")
        assert type(knowledge.get_storage()).__name__ == "SupabaseStorageBackend"
        monkeypatch.setattr(s, "vector_store", "pgvector")
        monkeypatch.setattr(s, "database_url", "sqlite+aiosqlite:///x.db")
        with pytest.raises(RuntimeError):
            knowledge.get_vector_store()
        knowledge.get_vector_store.cache_clear()
        monkeypatch.setattr(s, "database_url", "postgresql+asyncpg://u@h/db")
        assert type(knowledge.get_vector_store()).__name__ == "PgVectorStore"
    finally:
        for fn in (knowledge.get_storage, knowledge.get_vector_store):
            fn.cache_clear()


def test_pgvector_literal_and_index_sql():
    from app.knowledge.vectorstore.pgvector import _vec_literal, hnsw_index_sql
    assert _vec_literal([0.5, -1, 1e-9]) == "[0.5,-1,1e-09]"
    sql = hnsw_index_sql(384)
    assert "vector(384)" in sql and "WHERE dimension = 384" in sql and "IF NOT EXISTS" in sql


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL only (TEST_DATABASE_URL)")
async def test_pgvector_store_roundtrip():
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from app.knowledge.embeddings.mock import MockEmbeddingBackend
    from app.knowledge.vectorstore.pgvector import PgVectorStore

    eng = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
    vs = PgVectorStore(engine=eng)
    emb = MockEmbeddingBackend()
    course = f"t-{uuid.uuid4().hex[:8]}"
    texts = ["alpha loops", "beta lists", "gamma dicts"]
    vecs = await emb.embed_batch(texts)
    try:
        for i, (t, v) in enumerate(zip(texts, vecs)):
            await vs.upsert(course, emb.name, emb.dimension, f"c{i}", f"d{i % 2}", v, t, i, ["H", t], 3)
        await vs.upsert(course, emb.name, emb.dimension, "c0", "d0", vecs[0], "alpha loops v2", 0, [], 3)
        res = await vs.query(course, emb.name, emb.dimension, vecs[0], top_k=2)
        assert res[0].chunk_id == "c0" and res[0].text == "alpha loops v2" and res[0].score > 0.99
        assert len(res) == 2
        only = await vs.query(course, emb.name, emb.dimension, vecs[0], document_ids=["d1"])
        assert [m.chunk_id for m in only] == ["c1"] and only[0].heading_path == ["H", "beta lists"]
        assert await vs.query(course, emb.name, emb.dimension, vecs[0], document_ids=[]) == []
        assert (await vs.collection_info(course, emb.name))["count"] == 3
        await vs.delete_by_document(course, emb.name, "d0")
        assert (await vs.collection_info(course, emb.name))["count"] == 1
    finally:
        await vs.delete_by_document(course, emb.name, "d1")
        await eng.dispose()


@pytest.mark.skipif(not os.getenv("BOOKEY_TEST_FASTEMBED"), reason="downloads a model (BOOKEY_TEST_FASTEMBED=1)")
async def test_fastembed_backend_embeds():
    from app.knowledge.embeddings.fastembed_backend import FastEmbedBackend
    b = FastEmbedBackend()
    a, c, d = await b.embed_batch(["python for loop", "a for loop in python", "chocolate cake recipe"])
    assert len(a) == b.dimension == 384
    dot = lambda x, y: sum(p * q for p, q in zip(x, y))  # noqa: E731
    assert dot(a, c) > dot(a, d)


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs ffmpeg")
def test_media_transcription_falls_back_to_groq_chunks(tmp_path):
    import math
    import struct
    import wave as wav_mod
    import respx
    from app.knowledge.sources.media import transcribe_file_groq

    path = tmp_path / "a.wav"
    with wav_mod.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 8))) for i in range(16000 * 130)))
    calls = []
    with respx.mock:
        def reply(request):
            calls.append(request)
            return httpx.Response(200, json={"language": "english",
                                             "segments": [{"start": 1.0, "end": 2.5, "text": f" part {len(calls)} "}]})
        respx.post("https://api.groq.com/openai/v1/audio/transcriptions").mock(side_effect=reply)
        state: dict = {}
        tr = transcribe_file_groq(str(path), "k", "whisper-large-v3-turbo", state, chunk_seconds=60)
    assert len(calls) == 3 and calls[0].headers["authorization"] == "Bearer k"
    assert [s.text for s in tr.segments] == ["part 1", "part 2", "part 3"]
    assert [s.start for s in tr.segments] == [1.0, 61.0, 121.0]
    assert tr.language == "en" and tr.device == "groq" and state["pos"] == pytest.approx(130.0)
