"""
app/knowledge/storage/supabase.py — Supabase Storage over its REST API (production).

STORAGE_BACKEND=supabase. Uses the project's service_role (or sb_secret_…) key against a
PRIVATE bucket, so files are never publicly readable; the API streams them to learners
after its own auth checks. No S3 access keys needed (Supabase only issues those in the
dashboard).

Env: SUPABASE_URL (https://<ref>.supabase.co), SUPABASE_SERVICE_ROLE_KEY,
     SUPABASE_STORAGE_BUCKET (default "bookey").
Free tier: 1 GB total, 50 MB per object.
"""
from __future__ import annotations

import mimetypes
from urllib.parse import quote

import httpx

from app.knowledge.storage import StorageBackend


class SupabaseStorageBackend(StorageBackend):
    def __init__(self, url: str, service_key: str, bucket: str = "bookey",
                 timeout: float = 120.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not url or not service_key:
            raise RuntimeError("STORAGE_BACKEND=supabase needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
        self._base = url.rstrip("/") + "/storage/v1"
        self._bucket = bucket
        self._headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}
        self._timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=self._timeout, transport=self._transport)

    def _obj(self, key: str) -> str:
        safe = key.lstrip("/").replace("..", "__")
        return f"{self._base}/object/{quote(self._bucket)}/{quote(safe)}"

    async def store(self, key: str, data: bytes) -> None:
        ctype = mimetypes.guess_type(key)[0] or "application/octet-stream"
        async with self._client() as c:
            r = await c.post(self._obj(key), content=data,
                             headers={"Content-Type": ctype, "x-upsert": "true"})
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase storage upload failed ({r.status_code}): {r.text[:200]}")

    async def retrieve(self, key: str) -> bytes:
        async with self._client() as c:
            r = await c.get(self._obj(key))
        if r.status_code in (400, 404):
            raise FileNotFoundError(f"Storage key not found: {key}")
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase storage download failed ({r.status_code})")
        return r.content

    async def delete(self, key: str) -> None:
        async with self._client() as c:
            r = await c.request("DELETE", f"{self._base}/object/{quote(self._bucket)}",
                                json={"prefixes": [key.lstrip("/").replace("..", "__")]})
        if r.status_code >= 300 and r.status_code not in (400, 404):
            raise RuntimeError(f"Supabase storage delete failed ({r.status_code})")

    async def exists(self, key: str) -> bool:
        safe = key.lstrip("/").replace("..", "__")
        async with self._client() as c:
            r = await c.get(f"{self._base}/object/info/{quote(self._bucket)}/{quote(safe)}")
        return r.status_code == 200
