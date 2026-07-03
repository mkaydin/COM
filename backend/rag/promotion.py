"""Promotion queue — the seam between raw RAG chunks and curated OKF concepts.

Per spec §2.6: "Push candidates to a review queue (human approval, or
agent-approval with a confidence threshold) before writing into the OKF/Wiki
layer, to avoid polluting your curated knowledge with low-quality auto-summaries."

Per spec §5: "Never: Promote an ai_summary-tier item to OKF without a review step."

Flow:
  1. Candidate creation — gap detection, summarization, or manual flag.
  2. Review queue — list pending candidates.
  3. Approve — writes OKF concept via okf.bundle.file_reference, marks chunk.
  4. Reject — marks candidate as discarded.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PromotionCandidate, RagChunk
from logging_config import get_logger

logger = get_logger(__name__)


async def create_candidate(
    db: AsyncSession,
    *,
    chunk_id: str | UUID,
    reason: str = "manual",
    trigger_query: str = "",
    summary: str = "",
) -> dict:
    """Flag a chunk as a promotion candidate.

    Idempotent: if a pending candidate already exists for this chunk, returns
    it without creating a duplicate.
    """
    chunk_uuid = UUID(str(chunk_id)) if isinstance(chunk_id, str) else chunk_id

    # Check for existing pending candidate (idempotent).
    existing = await db.execute(
        select(PromotionCandidate)
        .where(PromotionCandidate.chunk_id == chunk_uuid)
        .where(PromotionCandidate.status == "pending")
        .limit(1)
    )
    found = existing.scalar_one_or_none()
    if found is not None:
        return {"status": "exists", "candidate_id": str(found.id)}

    candidate = PromotionCandidate(
        id=uuid.uuid4() if False else __import__("uuid").uuid4(),
        chunk_id=chunk_uuid,
        reason=reason,
        trigger_query=trigger_query[:500] if trigger_query else None,
        summary=summary[:5000] if summary else None,
        status="pending",
    )
    db.add(candidate)
    await db.commit()

    logger.info(
        "promotion_candidate_created",
        chunk_id=str(chunk_uuid)[:8],
        reason=reason,
    )
    return {"status": "created", "candidate_id": str(candidate.id)}


async def list_pending(
    db: AsyncSession,
    limit: int = 50,
) -> list[dict]:
    """List pending promotion candidates with their chunk metadata."""
    result = await db.execute(
        select(PromotionCandidate, RagChunk)
        .join(RagChunk, PromotionCandidate.chunk_id == RagChunk.id)
        .where(PromotionCandidate.status == "pending")
        .order_by(PromotionCandidate.created_at.desc())
        .limit(limit)
    )
    rows = result.all()

    candidates = []
    for cand, chunk in rows:
        candidates.append({
            "candidate_id": str(cand.id),
            "chunk_id": str(chunk.id),
            "reason": cand.reason,
            "trigger_query": cand.trigger_query or "",
            "summary": (cand.summary or "")[:500],
            "chunk_title": chunk.title,
            "chunk_section": chunk.section or "",
            "chunk_text": (chunk.chunk_text or "")[:500],
            "trust_tier": chunk.trust_tier,
            "source_type": chunk.source_type,
            "source_url": chunk.source_url or "",
            "tags": chunk.tags or [],
            "created_at": str(cand.created_at),
        })
    return candidates


async def approve(
    db: AsyncSession,
    *,
    candidate_id: str | UUID,
    title: Optional[str] = None,
    description: Optional[str] = None,
    body_override: Optional[str] = None,
) -> dict:
    """Approve a candidate: writes an OKF concept and marks the chunk promoted.

    Per spec §5: "Review promotion queue → approve or discard candidate OKF
    concepts." This is the ONLY path from raw → curated.
    """
    from okf.bundle import file_reference, BUNDLE_ROOT
    from okf.context import set_context
    from okf.index import regenerate_indexes

    cand_uuid = UUID(str(candidate_id)) if isinstance(candidate_id, str) else candidate_id

    # Fetch candidate + chunk.
    result = await db.execute(
        select(PromotionCandidate, RagChunk)
        .join(RagChunk, PromotionCandidate.chunk_id == RagChunk.id)
        .where(PromotionCandidate.id == cand_uuid)
        .limit(1)
    )
    row = result.first()
    if row is None:
        return {"status": "error", "error": "Candidate not found"}

    cand, chunk = row
    if cand.status != "pending":
        return {"status": "error", "error": f"Candidate already {cand.status}"}

    # Build the OKF concept content.
    concept_title = title or chunk.title[:200]
    concept_desc = description or (chunk.chunk_text[:200].replace("\n", " ").strip() or chunk.title)
    concept_body = body_override or cand.summary or chunk.chunk_text[:4000]

    # Write to the OKF bundle.
    set_context(BUNDLE_ROOT)
    write_result = file_reference(
        title=concept_title,
        description=concept_desc,
        body=concept_body,
        resource=chunk.source_url or "",
        tags=list(chunk.tags or [])[:10],
    )

    if "error" in write_result:
        return {"status": "error", "error": write_result["error"]}

    okf_ref = write_result.get("path", "")

    # Mark candidate as approved.
    await db.execute(
        update(PromotionCandidate)
        .where(PromotionCandidate.id == cand_uuid)
        .values(
            status="approved",
            okf_concept_ref=okf_ref,
            reviewed_at=datetime.now(timezone.utc),
        )
    )

    # Mark the chunk as promoted.
    await db.execute(
        update(RagChunk)
        .where(RagChunk.id == chunk.id)
        .values(promoted_to_okf=True, okf_concept_ref=okf_ref)
    )

    await db.commit()

    # Regenerate OKF indexes.
    try:
        await regenerate_indexes(BUNDLE_ROOT)
    except Exception as e:
        logger.warning("promotion_index_regen_failed", error=str(e)[:100])

    logger.info(
        "promotion_approved",
        candidate_id=str(cand_uuid)[:8],
        okf_ref=okf_ref,
        chunk=str(chunk.id)[:8],
    )
    return {"status": "approved", "okf_concept_ref": okf_ref}


async def reject(
    db: AsyncSession,
    *,
    candidate_id: str | UUID,
) -> dict:
    """Reject a promotion candidate (discard without writing to OKF)."""
    cand_uuid = UUID(str(candidate_id)) if isinstance(candidate_id, str) else candidate_id

    result = await db.execute(
        select(PromotionCandidate)
        .where(PromotionCandidate.id == cand_uuid)
        .limit(1)
    )
    cand = result.scalar_one_or_none()
    if cand is None:
        return {"status": "error", "error": "Candidate not found"}
    if cand.status != "pending":
        return {"status": "error", "error": f"Candidate already {cand.status}"}

    await db.execute(
        update(PromotionCandidate)
        .where(PromotionCandidate.id == cand_uuid)
        .values(status="rejected", reviewed_at=datetime.now(timezone.utc))
    )
    await db.commit()

    logger.info("promotion_rejected", candidate_id=str(cand_uuid)[:8])
    return {"status": "rejected"}


async def flag_gap_candidates(
    db: AsyncSession,
    query: str,
    chunk_ids: list[str],
) -> int:
    """Auto-flag chunks as promotion candidates when gap detection fires.

    Per spec §4.5: "If a gap is found repeatedly (same query hits vector
    fallback 3+ times with no curated match), flag it for promotion review."
    """
    count = 0
    for cid in chunk_ids[:3]:  # flag top 3 chunks max
        result = await create_candidate(
            db,
            chunk_id=cid,
            reason="gap_detection",
            trigger_query=query,
        )
        if result["status"] == "created":
            count += 1
    return count
