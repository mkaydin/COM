"""Document ingestion for uploaded files and saved divinations.

Converts a document into retrievable chunks. This delegates to
`rag.ingest.ingest`, which owns structure-aware chunking, batched embedding
through the configured backend, dedupe and trust tiers.

This module used to carry its own copy of that pipeline, and the copy was
broken in four ways at once:

* each chunk's embedding was computed, checked for emptiness, and then never
  passed to the row — the vector was discarded, so `documents.embedding`
  stayed NULL while the log reported `pgvector_stored`;
* that column is `Vector(1536)` while the embedder emits `VECTOR_DIM` (384), so
  storing it would have failed even if it had been passed;
* the rows went to `documents`, which retrieval never queries — retrieval reads
  `rag_chunks`;
* a second copy went to ChromaDB, which nothing reads (`collection.query`
  appears nowhere in the codebase) and which embedded with Chroma's own
  default model, a different vector space from the configured one.

The visible effect was that a saved divination was filed, reported success, and
was invisible to every retrieval path.
"""
from __future__ import annotations

from logging_config import get_logger

logger = get_logger(__name__)


async def ingest_document(
    doc_id: str,
    title: str,
    content: str,
    source_type: str = "user_upload",
) -> dict:
    """Chunk, embed and store a document so retrieval can find it.

    Returns `rag.ingest.ingest`'s result: `{"status", "chunks",
    "content_hash", "trust_tier"}`.
    """
    from db.postgres import async_session
    from rag.ingest import ingest as rag_ingest

    if not content or not content.strip():
        logger.info("document_ingest_skipped", doc_id=doc_id, reason="empty content")
        return {"status": "skipped", "chunks": 0, "reason": "empty content"}

    async with async_session() as db:
        result = await rag_ingest(
            db,
            content=content,
            title=title,
            source_type=source_type,
            source_doc_id=doc_id,
        )

    logger.info(
        "document_ingested",
        doc_id=doc_id,
        source_type=source_type,
        chunks=result.get("chunks", 0),
        status=result.get("status"),
    )
    return result