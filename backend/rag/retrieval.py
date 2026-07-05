"""Unified retrieval layer per RAG spec §4.

Implements the three-tier retrieval flow:
  1. Query OKF bundle (curated) → highest trust.
  2. Query Sage wiki (curated) → highest trust.
  3. Fall back to vector search over rag_chunks (raw) → weighted by trust_tier.

Results are merged and ranked. Raw chunks always carry source_url + trust_tier
for citation. Gap detection flags queries that repeatedly hit the vector
fallback with no curated match (spec §4.5 — promotion signal).
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import RagChunk
from rag.ingest import TRUST_WEIGHTS
from logging_config import get_logger

logger = get_logger(__name__)

# Curated sources get the highest possible trust weight.
_CURATED_WEIGHT = 1.0
_CURATED_TIER = "curated"

# Gap detection: track queries that hit vector fallback with no curated match.
# key = query_hash, value = count. When count >= GAP_THRESHOLD → flag for promotion.
_GAP_THRESHOLD = 3
_gap_tracker: dict[str, int] = {}


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.lower().strip().encode()).hexdigest()[:16]


async def _retrieve_okf(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Query the OKF bundle (curated knowledge)."""
    try:
        from okf.bundle import retrieve_relevant, BUNDLE_ROOT, load_concept_body
        concepts = await asyncio.to_thread(retrieve_relevant, query, BUNDLE_ROOT, top_k)
        results = []
        for c in concepts:
            body = await asyncio.to_thread(load_concept_body, c["id"], BUNDLE_ROOT)
            results.append({
                "source_system": "okf",
                "id": c["id"],
                "title": c["title"],
                "description": c.get("description", ""),
                "content": body[:2000] if body else c.get("description", ""),
                "score": c.get("score", 0.0),
                "trust_tier": _CURATED_TIER,
                "trust_weight": _CURATED_WEIGHT,
            })
        return results
    except Exception as e:
        logger.warning("okf_retrieval_failed", error=str(e)[:100])
        return []


def _retrieve_wiki(query: str, max_pages: int = 5) -> list[dict[str, Any]]:
    """Query the Sage wiki (curated knowledge)."""
    try:
        from agents.sage_wiki import search_wiki
        pages = search_wiki(query, max_pages)
        results = []
        for p in pages:
            results.append({
                "source_system": "wiki",
                "id": p["path"],
                "title": p["title"],
                "description": "",
                "content": p["content"][:2000],
                "score": 0.9,  # wiki doesn't do semantic scoring; keyword match
                "trust_tier": _CURATED_TIER,
                "trust_weight": _CURATED_WEIGHT,
            })
        return results
    except Exception as e:
        logger.warning("wiki_retrieval_failed", error=str(e)[:100])
        return []


async def _vector_search(
    db: AsyncSession,
    query_vec: list[float],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Vector search over rag_chunks (raw knowledge), ranked by trust_tier.

    Spec §4: merge results, preferring higher trust_tier when chunks conflict.
    final_score = cosine_similarity * trust_weight.
    """
    stmt = (
        select(RagChunk, RagChunk.embedding.cosine_distance(query_vec).label("distance"))
        .where(RagChunk.embedding.isnot(None))
        .where(RagChunk.superseded_by.is_(None))
        .order_by("distance")
        .limit(top_k * 3)  # over-fetch, then re-rank by trust
    )
    result = await db.execute(stmt)
    rows = result.all()

    scored = []
    for chunk, distance in rows:
        cosine_sim = max(0.0, 1.0 - float(distance))
        trust_tier = chunk.trust_tier or "web_secondary"
        trust_weight = TRUST_WEIGHTS.get(trust_tier, 0.5)
        final_score = cosine_sim * trust_weight

        scored.append({
            "source_system": "rag",
            "id": str(chunk.id),
            "title": chunk.title,
            "section": chunk.section or "",
            "text": chunk.chunk_text,
            "parent_section": (chunk.parent_section_text or "")[:1500],
            "content": chunk.chunk_text,  # alias for unified formatting
            "cosine_similarity": cosine_sim,
            "score": final_score,
            "trust_tier": trust_tier,
            "trust_weight": trust_weight,
            "source_type": chunk.source_type,
            "source_url": chunk.source_url,
            "tags": chunk.tags or [],
            "promoted_to_okf": chunk.promoted_to_okf,
        })

    # Re-sort by trust-weighted score (already from DB order, but trust re-ranking matters).
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:top_k]


def _format_for_context(results: list[dict[str, Any]], max_chars: int = 8000) -> str:
    """Format retrieval results into an LLM context block.

    Curated sources (OKF/Wiki) are presented first; raw RAG chunks follow
    with their trust_tier annotation so the consuming agent can distinguish
    peer_reviewed from ai_summary.
    """
    if not results:
        return "(No knowledge retrieved from any system.)"

    curated = [r for r in results if r["trust_tier"] == _CURATED_TIER]
    raw = [r for r in results if r["trust_tier"] != _CURATED_TIER]

    parts: list[str] = []

    if curated:
        parts.append("=== CURATED KNOWLEDGE (vetted — highest trust) ===")
        for r in curated:
            parts.append(
                f"[{r['source_system'].upper()}] {r['title']}\n"
                f"{r.get('content', r.get('text', ''))[:1200]}"
            )

    if raw:
        parts.append("\n=== RAW KNOWLEDGE (unreviewed — trust_tier annotated) ===")
        for r in raw:
            cite = f" (source: {r['source_url']})" if r.get("source_url") else ""
            parts.append(
                f"[{r['trust_tier'].upper()}] {r['title']} / {r.get('section', '')}{cite}\n"
                f"{r.get('text', '')[:1000]}"
            )

    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[context truncated]"
    return text


async def retrieve(
    db: AsyncSession,
    query: str,
    top_k: int = 10,
) -> dict[str, Any]:
    """Unified retrieval per spec §4.

    Returns:
        {
            "query": str,
            "curated": [...],   # OKF + Wiki results
            "raw": [...],       # RAG vector search results
            "all": [...],       # merged + ranked
            "context": str,     # formatted LLM context block
            "stats": {
                "curated_count": int,
                "raw_count": int,
                "has_curated_match": bool,
                "gap_detected": bool,
                "top_trust_tier": str | None,
            },
        }
    """
    # 1. Query OKF bundle (curated).
    okf_results = await _retrieve_okf(query, top_k=5)

    # 2. Query Sage wiki (curated).
    wiki_results = await asyncio.to_thread(_retrieve_wiki, query, 5)

    curated = okf_results + wiki_results
    has_curated_match = len(curated) > 0

    # 3. Fall back to vector search over rag_chunks.
    from embedding import embed_text
    query_vec = embed_text(query)
    raw_results = await _vector_search(db, query_vec, top_k=top_k)

    # 4. Gap detection (spec §4.5): track queries with no curated match.
    #    When threshold reached, auto-flag top raw chunks for promotion review.
    qhash = _query_hash(query)
    gap_detected = False
    if not has_curated_match and raw_results:
        _gap_tracker[qhash] = _gap_tracker.get(qhash, 0) + 1
        if _gap_tracker[qhash] >= _GAP_THRESHOLD:
            gap_detected = True
            logger.info(
                "rag_gap_detected",
                query=query[:60],
                count=_gap_tracker[qhash],
                hint="candidate for OKF promotion",
            )
            # Auto-flag top raw chunks as promotion candidates.
            try:
                from rag.promotion import flag_gap_candidates
                chunk_ids = [r["id"] for r in raw_results[:3]]
                flagged = await flag_gap_candidates(db, query, chunk_ids)
                if flagged:
                    logger.info("promotion_auto_flagged", count=flagged, query=query[:50])
            except Exception as e:
                logger.warning("promotion_auto_flag_failed", error=str(e)[:100])
    elif has_curated_match:
        # Reset gap counter if curated match found.
        _gap_tracker.pop(qhash, None)

    # 5. Merge + rank (curated always ranks above raw per spec §4).
    all_results = curated + raw_results

    # Determine top trust tier.
    top_tier = None
    if curated:
        top_tier = _CURATED_TIER
    elif raw_results:
        top_tier = raw_results[0]["trust_tier"]

    # 6. Format context block.
    context = _format_for_context(all_results)

    stats = {
        "curated_count": len(curated),
        "raw_count": len(raw_results),
        "has_curated_match": has_curated_match,
        "gap_detected": gap_detected,
        "top_trust_tier": top_tier,
        "okf_count": len(okf_results),
        "wiki_count": len(wiki_results),
    }

    logger.info(
        "rag_retrieval_complete",
        query=query[:50],
        okf=len(okf_results),
        wiki=len(wiki_results),
        raw=len(raw_results),
        gap=gap_detected,
    )

    return {
        "query": query,
        "curated": curated,
        "raw": raw_results,
        "all": all_results,
        "context": context,
        "stats": stats,
    }


def get_gap_candidates() -> list[dict]:
    """Return queries flagged for promotion review (gap detection)."""
    return [
        {"query_hash": h, "fallback_count": c}
        for h, c in _gap_tracker.items()
        if c >= _GAP_THRESHOLD
    ]


def reset_gap(query: str) -> None:
    """Manually reset gap tracking for a query (after promotion)."""
    _gap_tracker.pop(_query_hash(query), None)


# --- Supporter mode (Phase 3) -----------------------------------------------
# Used by Sage + Oracle to inject raw RAG chunks alongside their own curated
# knowledge systems. Searches ONLY the rag_chunks vector store — does NOT
# duplicate OKF/Wiki queries the agents already perform themselves.


async def retrieve_raw_context(
    db: AsyncSession,
    query: str,
    top_k: int = 5,
    max_chars: int = 4000,
) -> str:
    """Retrieve raw RAG chunks and format as an annotated context block.

    This is the SUPPORTER function — call it from agent nodes (Sage, Oracle)
    to inject supplementary raw evidence alongside their curated knowledge.
    Returns a formatted string with trust_tier annotations, or "" on failure.
    """
    try:
        from embedding import embed_text
        query_vec = embed_text(query)
        raw = await _vector_search(db, query_vec, top_k=top_k)
        if not raw:
            return ""

        parts = ["=== RAW KNOWLEDGE (RAG supporter — unreviewed, trust_tier annotated) ==="]
        for r in raw:
            cite = f" (source: {r['source_url']})" if r.get("source_url") else ""
            parts.append(
                f"[{r['trust_tier'].upper()}] {r['title']} / {r.get('section', '')}{cite}\n"
                f"{r.get('text', '')[:800]}"
            )

        text = "\n\n".join(parts)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[RAG context truncated]"
        return text
    except Exception as e:
        logger.warning("rag_supporter_failed", error=str(e)[:120])
        return ""
