"""
app/knowledge/storage/__init__.py

StorageBackend interface (Strategy pattern).

Implementations:
  - LocalStorageBackend — dev: writes to STORAGE_ROOT dir
  - S3StorageBackend    — prod: Supabase Storage / Cloudflare R2 via boto3

Selection: settings.storage_backend = "local" | "s3"
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class StorageBackend(ABC):
    """Abstract file storage. All methods are async-safe (run in thread pool)."""

    @abstractmethod
    async def store(self, key: str, data: bytes) -> None:
        """Persist *data* at *key*. Overwrites if key exists."""

    @abstractmethod
    async def retrieve(self, key: str) -> bytes:
        """Return bytes stored at *key*. Raises FileNotFoundError if missing."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete *key*. No-op if not found."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return True if *key* exists."""

    # ── large files (video uploads) — optional, with byte-based defaults ──────────

    async def store_file(self, key: str, path: str) -> None:
        """Persist a local file at *key* (backends may stream/copy instead of reading it all)."""
        import asyncio
        from pathlib import Path
        await self.store(key, await asyncio.to_thread(Path(path).read_bytes))

    async def local_path(self, key: str) -> str | None:
        """A readable local filesystem path for *key*, or None (caller then uses retrieve())."""
        return None
