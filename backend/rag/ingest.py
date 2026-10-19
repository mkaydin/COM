"""RAG ingestion pipeline per spec §2 + §5.

Flow: parse → hash → dedupe check → structure-aware chunk → embed → store.

Dedupe rules (spec §2.3):
  - Exact duplicate (same content_hash): skip, update last_seen.
  - Near-duplicate (same title, different hash): keep both, link with supersedes.
  - Never silently overwrite — science content revisions matter.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import RagChunk
from rag.chunker import chunk_document, Chunk
from logging_config import get_logger

logger = get_logger(__name__)

# Trust-tier → numeric weight for retrieval ranking (spec §4: prefer higher trust_tier).
TRUST_WEIGHTS = {
    "peer_reviewed": 1.0,
    "preprint": 0.85,
    "web_primary": 0.75,
    "web_secondary": 0.55,
    "ai_summary": 0.35,
}

DEFAULT_TRUST_BY_SOURCE = {
    "pdf": "preprint",
    "user_upload": "web_secondary",
    "web_scrape": "web_primary",
    "websearch_raw": "web_primary",
    "websearch_summary": "ai_summary",
    "divination": "ai_summary",
    "scout_report": "ai_summary",
}

# Cached embedding model.
_st_model = None


def _get_embedder():
    """The loaded sentence-transformer, for callers that need the object.

    Prefer `llm.embeddings.embed()` — this returns the model itself and so
    bypasses the selectable backend. Kept because `rag.retrieval` uses it as a
    last resort when the async path is unavailable.
    """
    from llm.embeddings import _get_local_model

    return _get_local_model()


def _embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts → list of vectors.

    Blocking: the encode is CPU-bound and loads the model on first use. Callers
    must be in a thread (`asyncio.to_thread`); `llm.embeddings.embed` does that.
    """
    from llm.embeddings import embed_sync

    return embed_sync(texts)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def ingest(
    db: AsyncSession,
    *,
    content: str,
    title: str,
    source_type: str = "user_upload",
    source_url: Optional[str] = None,
    authors: Optional[list[str]] = None,
    publish_date: Optional[datetime] = None,
    trust_tier: Optional[str] = None,
    tags: Optional[list[str]] = None,
    source_doc_id: Optional[str] = None,
) -> dict:
    """Ingest a document into the RAG chunk store.

    Returns {"status": "ingested"|"skipped"|"superseded", "chunks": N,
             "content_hash": "..."}.
    """
    if not content or not content.strip():
        return {"status": "skipped", "reason": "empty content", "chunks": 0}

    content = content.strip()
    chash = _content_hash(content)

    # --- Dedupe check (spec §2.3) ---
    existing = await db.execute(
        select(RagChunk).where(RagChunk.content_hash == chash).limit(1)
    )
    existing_chunk = existing.scalar_one_or_none()
    if existing_chunk is not None:
        # Exact duplicate: update last_seen, skip.
        await db.execute(
            update(RagChunk)
            .where(RagChunk.content_hash == chash)
            .values(last_seen=datetime.now(timezone.utc))
        )
        await db.commit()
        logger.info("rag_dedupe_skip", hash=chash[:12], title=title[:50])
        return {"status": "skipped", "reason": "exact duplicate", "chunks": 0,
                "content_hash": chash}

    # Near-duplicate check: same title, different hash → supersedes.
    near_dup = await db.execute(
        select(RagChunk)
        .where(RagChunk.title == title[:512])
        .where(RagChunk.superseded_by.is_(None))
        .limit(1)
    )
    near_dup_chunk = near_dup.scalar_one_or_none()
    supersedes_id = None
    if near_dup_chunk is not None and near_dup_chunk.content_hash != chash:
        supersedes_id = near_dup_chunk.id
        logger.info("rag_supersede", old_hash=near_dup_chunk.content_hash[:12],
                     new_hash=chash[:12], title=title[:50])

    # --- Structure-aware chunking (spec §2.4) ---
    raw_chunks: list[Chunk] = chunk_document(content)
    if not raw_chunks:
        return {"status": "skipped", "reason": "no chunks produced", "chunks": 0}

    # --- Embed (batch) ---
    texts_to_embed = [c.text for c in raw_chunks]
    embeddings = await asyncio.to_thread(_embed, texts_to_embed)

    # --- Resolve trust tier ---
    if trust_tier is None:
        trust_tier = DEFAULT_TRUST_BY_SOURCE.get(source_type, "web_secondary")

    now = datetime.now(timezone.utc)

    # --- Store with parent-child links ---
    # First pass: insert all chunks (parent_chunk_id filled in second pass).
    created: list[RagChunk] = []
    for i, rchunk in enumerate(raw_chunks):
        emb = embeddings[i] if i < len(embeddings) else None
        row = RagChunk(
            id=uuid.uuid4(),
            chunk_text=rchunk.text,
            embedding=emb,
            section=rchunk.section,
            chunk_index=i,
            parent_section_text=rchunk.parent_section_text,
            parent_chunk_id=None,  # Set in second pass (see below).
            source_type=source_type,
            source_url=source_url,
            title=title[:512],
            authors=authors,
            publish_date=publish_date,
            content_hash=chash,
            supersedes=supersedes_id,
            superseded_by=None,
            trust_tier=trust_tier,
            tags=tags or [],
            promoted_to_okf=False,
            okf_concept_ref=None,
            source_doc_id=uuid.UUID(source_doc_id) if source_doc_id else None,
            created_at=now,
            last_seen=now,
        )
        db.add(row)
        created.append(row)

    # Second pass: link parent_chunk_id — the first chunk of each section
    # becomes the parent for subsequent chunks in that section.
    section_parents: dict[str, uuid.UUID] = {}
    for row, rchunk in zip(created, raw_chunks):
        if rchunk.section not in section_parents:
            section_parents[rchunk.section] = row.id
            # A chunk can be its own parent if it's the only one in the section.
            row.parent_chunk_id = row.id
        else:
            row.parent_chunk_id = section_parents[rchunk.section]

    # Mark ALL chunks of the old version as superseded (spec §2.3: never
    # silently overwrite — link versions so old chunks can be suppressed).
    if supersedes_id is not None:
        old_hash = near_dup_chunk.content_hash
        await db.execute(
            update(RagChunk)
            .where(RagChunk.content_hash == old_hash)
            .where(RagChunk.superseded_by.is_(None))
            .values(superseded_by=created[0].id)
        )

    await db.commit()

    logger.info(
        "rag_ingested",
        title=title[:50],
        chunks=len(created),
        hash=chash[:12],
        trust=trust_tier,
        superseded=bool(supersedes_id),
    )
    return {
        "status": "superseded" if supersedes_id else "ingested",
        "chunks": len(created),
        "content_hash": chash,
        "trust_tier": trust_tier,
    }


async def ingest_batch(db: AsyncSession, documents: list[dict]) -> dict:
    """Ingest multiple documents. Each dict must have: content, title.
    Optional: source_type, source_url, authors, publish_date, trust_tier,
    tags, source_doc_id.
    """
    stats = {"ingested": 0, "skipped": 0, "superseded": 0, "chunks": 0}
    for doc in documents:
        result = await ingest(db, **doc)
        stats["chunks"] += result.get("chunks", 0)
        if result["status"] == "ingested":
            stats["ingested"] += 1
        elif result["status"] == "superseded":
            stats["superseded"] += 1
        else:
            stats["skipped"] += 1
    return stats
