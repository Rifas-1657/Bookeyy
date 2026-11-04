"""
app/knowledge/embeddings/fastembed_backend.py

CPU embedding backend via fastembed (ONNX Runtime, no torch) — the production default.

Why: the free production host has 512 MB RAM and no GPU, so BGE-M3 / sentence-transformers
(torch, ~2 GB) do not fit, and the hosted embedding APIs we have keys for are not usable for
free right now. fastembed + BAAI/bge-small-en-v1.5 (quantized ONNX, ~67 MB, 384-dim) runs in
roughly 100-150 MB RSS with one thread. Set FASTEMBED_MODEL to
"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2" (384-dim, multilingual,
~220 MB) on a larger box.

- Lazy: the model loads on the first embed call, never at import time.
- Normalized vectors (cosine-ready). Name/dimension are recorded per index (no mixing).
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from app.knowledge.embeddings import EmbeddingBackend

_KNOWN_DIMS = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": 384,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "intfloat/multilingual-e5-large": 1024,
}

_lock = threading.Lock()
_models: dict[tuple[str, str, int], Any] = {}


def _dimension_for(model: str) -> int:
    if model in _KNOWN_DIMS:
        return _KNOWN_DIMS[model]
    from fastembed import TextEmbedding  # only for unknown models
    for info in TextEmbedding.list_supported_models():
        if info.get("model") == model:
            return int(info["dim"])
    raise ValueError(f"Unknown fastembed model: {model}")


def _load(model: str, cache_dir: str, threads: int) -> Any:
    key = (model, cache_dir, threads)
    with _lock:
        if key not in _models:
            from fastembed import TextEmbedding
            kwargs: dict[str, Any] = {"model_name": model, "threads": threads or None}
            if cache_dir:
                kwargs["cache_dir"] = cache_dir
            _models[key] = TextEmbedding(**kwargs)
        return _models[key]


class FastEmbedBackend(EmbeddingBackend):
    def __init__(self, model: str = "BAAI/bge-small-en-v1.5", cache_dir: str = "",
                 threads: int = 1, batch_size: int = 16) -> None:
        self._model = model
        self._cache_dir = cache_dir
        self._threads = threads
        self._batch_size = max(1, batch_size)
        self.name = f"fastembed/{model}"
        self.dimension = _dimension_for(model)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        import numpy as np
        m = _load(self._model, self._cache_dir, self._threads)
        out: list[list[float]] = []
        for vec in m.embed(texts, batch_size=self._batch_size):
            v = np.asarray(vec, dtype="float32")
            n = float(np.linalg.norm(v)) or 1.0
            out.append((v / n).tolist())
        return out

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_sync, texts)
