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
from typing import NamedTuple, Optional

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
    # A filed debate is model output, not a peer-reviewed source. It is still
    # worth retrieving — it is the Council's own reasoning — but it must rank
    # below primary material.
    "forum_topic": "ai_summary",
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


class _Dedupe(NamedTuple):
    """Outcome of the duplicate checks.

    `early` is set when the document is an exact duplicate and the caller should
    return it without embedding anything.
    """

    early: Optional[dict]
    supersedes_id: Optional[uuid.UUID]
    near_dup: Optional[RagChunk]


async def _dedupe(db: AsyncSession, chash: str, title: str) -> _Dedupe:
    """Exact-duplicate and near-duplicate checks (spec §2.3).

    An exact duplicate only updates `last_seen` and is skipped. A near-duplicate
    (same title, different content) is kept and linked, never overwritten:
    revisions of scientific content matter.
    """
    existing = await db.execute(
        select(RagChunk).where(RagChunk.content_hash == chash).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        await db.execute(
            update(RagChunk)
            .where(RagChunk.content_hash == chash)
            .values(last_seen=datetime.now(timezone.utc))
        )
        await db.commit()
        logger.info("rag_dedupe_skip", hash=chash[:12], title=title[:50])
        return _Dedupe(
            early={
                "status": "skipped", "reason": "exact duplicate",
                "chunks": 0, "content_hash": chash,
            },
            supersedes_id=None,
            near_dup=None,
        )

    near_dup = (
        await db.execute(
            select(RagChunk)
            .where(RagChunk.title == title[:512])
            .where(RagChunk.superseded_by.is_(None))
            .limit(1)
        )
    ).scalar_one_or_none()

    supersedes_id = None
    if near_dup is not None and near_dup.content_hash != chash:
        supersedes_id = near_dup.id
        logger.info(
            "rag_supersede",
            old_hash=near_dup.content_hash[:12],
            new_hash=chash[:12],
            title=title[:50],
        )
    return _Dedupe(early=None, supersedes_id=supersedes_id, near_dup=near_dup)


def _build_chunk_rows(
    db: AsyncSession,
    raw_chunks: list[Chunk],
    embeddings: list[list[float]],
    embedder: Optional[str],
    *,
    chash: str,
    title: str,
    source_type: str,
    source_url: Optional[str],
    authors: Optional[list[str]],
    publish_date: Optional[datetime],
    trust_tier: str,
    tags: Optional[list[str]],
    source_doc_id: Optional[str],
    supersedes_id: Optional[uuid.UUID],
    now: datetime,
) -> list[RagChunk]:
    """Turn chunks plus vectors into rows, and hand them to the session."""
    created: list[RagChunk] = []
    for i, rchunk in enumerate(raw_chunks):
        emb = embeddings[i] if i < len(embeddings) else None
        row = RagChunk(
            id=uuid.uuid4(),
            chunk_text=rchunk.text,
            embedding=emb,
            # Which model made this vector. Without it a later change to
            # EMBEDDING_MODEL silently mixes incomparable vector spaces.
            embedding_model=embedder if emb is not None else None,
            section=rchunk.section,
            chunk_index=i,
            parent_section_text=rchunk.parent_section_text,
            parent_chunk_id=None,  # filled by _link_section_parents
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
    return created


def _link_section_parents(created: list[RagChunk], raw_chunks: list[Chunk]) -> None:
    """Point each chunk at its section's first chunk.

    A chunk can be its own parent when it is the only one in the section, which
    is what lets a caller expand a section without a special case.
    """
    section_parents: dict[str, uuid.UUID] = {}
    for row, rchunk in zip(created, raw_chunks):
        if rchunk.section not in section_parents:
            section_parents[rchunk.section] = row.id
            row.parent_chunk_id = row.id
        else:
            row.parent_chunk_id = section_parents[rchunk.section]


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

    dedupe = await _dedupe(db, chash, title)
    if dedupe.early is not None:
        return dedupe.early

    raw_chunks: list[Chunk] = chunk_document(content)
    if not raw_chunks:
        return {"status": "skipped", "reason": "no chunks produced", "chunks": 0}

    # One batched embed for the whole document.
    embeddings = await asyncio.to_thread(_embed, [c.text for c in raw_chunks])

    from llm.embeddings import identity as _embedding_identity

    embedder = _embedding_identity() if embeddings else None

    if trust_tier is None:
        trust_tier = DEFAULT_TRUST_BY_SOURCE.get(source_type, "web_secondary")

    now = datetime.now(timezone.utc)
    created = _build_chunk_rows(
        db, raw_chunks, embeddings, embedder,
        chash=chash, title=title, source_type=source_type, source_url=source_url,
        authors=authors, publish_date=publish_date, trust_tier=trust_tier,
        tags=tags, source_doc_id=source_doc_id,
        supersedes_id=dedupe.supersedes_id, now=now,
    )
    _link_section_parents(created, raw_chunks)

    # Mark ALL chunks of the old version as superseded (spec §2.3: never
    # silently overwrite — link versions so old chunks can be suppressed).
    if dedupe.supersedes_id is not None and dedupe.near_dup is not None:
        await db.execute(
            update(RagChunk)
            .where(RagChunk.content_hash == dedupe.near_dup.content_hash)
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
        superseded=bool(dedupe.supersedes_id),
    )
    return {
        "status": "superseded" if dedupe.supersedes_id else "ingested",
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
