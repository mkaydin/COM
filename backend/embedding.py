"""Centralized embedding client using Ollama.

Replaces sentence-transformers everywhere. Uses Ollama's OpenAI-compatible
/v1/embeddings endpoint with qwen3-embedding:0.6b (1024-dim).

All functions are synchronous (the Ollama HTTP call is fast enough that
async overhead isn't worth it). Where called from async contexts, wrap
with asyncio.to_thread().
"""
from __future__ import annotations

import httpx
from typing import List

from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        s = get_settings()
        base = f"http://{s.ollama_host}:{s.ollama_port}"
        _client = httpx.Client(base_url=base, timeout=60.0)
    return _client


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a batch of texts via Ollama. Returns list of 1024-dim vectors."""
    if not texts:
        return []
    s = get_settings()
    client = _get_client()

    # Ollama's /api/embeddings accepts one prompt at a time.
    # Batch by calling repeatedly (Ollama handles concurrent requests fine,
    # but sequential is simpler and fast enough for our chunk sizes).
    results: list[list[float]] = []
    for text in texts:
        try:
            resp = client.post("/api/embeddings", json={
                "model": s.embedding_model,
                "prompt": text[:8000],  # truncate very long texts
            })
            resp.raise_for_status()
            data = resp.json()
            emb = data.get("embedding", [])
            results.append(emb)
        except Exception as e:
            logger.warning("ollama_embed_failed", error=str(e)[:100], text_len=len(text))
            results.append([])

    return results


def embed_text(text: str) -> List[float]:
    """Embed a single text. Returns 1024-dim vector (empty list on failure)."""
    results = embed_texts([text])
    return results[0] if results else []


def embed_query(text: str):
    """Embed a query for vector search. Returns numpy-compatible list."""
    return embed_text(text)
