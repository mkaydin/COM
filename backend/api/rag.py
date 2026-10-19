"""RAG API — ingestion, search, promotion queue management."""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from logging_config import get_logger

from db.postgres import get_db
from db.models import RagChunk, PromotionCandidate

router = APIRouter(prefix="/api/rag", tags=["rag"])
logger = get_logger(__name__)


# --- Stats ---

@router.get("/stats")
async def rag_stats(db: AsyncSession = Depends(get_db)):
    """RAG layer statistics: chunk count, trust distribution, promotion queue."""
    total = (await db.execute(select(func.count(RagChunk.id)))).scalar() or 0
    with_emb = (await db.execute(select(func.count(RagChunk.id)).where(RagChunk.embedding.isnot(None)))).scalar() or 0
    promoted = (await db.execute(select(func.count(RagChunk.id)).where(RagChunk.promoted_to_okf == True))).scalar() or 0

    trust_rows = (await db.execute(
        select(RagChunk.trust_tier, func.count(RagChunk.id))
        .where(RagChunk.superseded_by.is_(None))
        .group_by(RagChunk.trust_tier)
    )).all()
    trust_dist = {row[0]: row[1] for row in trust_rows}

    pending = (await db.execute(
        select(func.count(PromotionCandidate.id)).where(PromotionCandidate.status == "pending")
    )).scalar() or 0

    return {
        "total_chunks": total,
        "with_embeddings": with_emb,
        "promoted_to_okf": promoted,
        "pending_promotions": pending,
        "trust_distribution": trust_dist,
    }


# --- Search ---

class SearchRequest(BaseModel):
    query: str
    top_k: int = 10


@router.post("/search")
async def rag_search(req: SearchRequest, db: AsyncSession = Depends(get_db)):
    """Unified retrieval search (OKF → Wiki → vector fallback).

    Degrades instead of 500ing when the embedder is unavailable — an offline
    first boot cannot download the model, and every caller of `retrieve`
    treats a missing vector tier as "no raw results". The agents already
    handle that; this endpoint is the one that did not.
    """
    from rag.retrieval import retrieve
    try:
        result = await retrieve(db, req.query, top_k=req.top_k)
    except Exception as e:
        from llm import diagnose_call_error

        # `diagnose_call_error` takes the message, not the exception.
        hint = diagnose_call_error(str(e))
        logger.warning("rag_search_degraded", error=str(e)[:200])
        return {
            "query": req.query,
            "curated": [],
            "raw": [],
            "all": [],
            "context": "",
            "degraded": True,
            "reason": hint or str(e)[:200],
            "stats": {"curated": 0, "raw": 0},
        }
    return result


# --- Ingest ---

class IngestRequest(BaseModel):
    content: str
    title: str
    source_type: str = "user_upload"
    source_url: Optional[str] = None
    trust_tier: Optional[str] = None
    tags: list[str] = []


@router.post("/ingest")
async def rag_ingest(req: IngestRequest, db: AsyncSession = Depends(get_db)):
    """Ingest raw text into the RAG chunk store."""
    from rag.ingest import ingest
    result = await ingest(
        db,
        content=req.content,
        title=req.title,
        source_type=req.source_type,
        source_url=req.source_url,
        trust_tier=req.trust_tier,
        tags=req.tags,
    )
    return result


# --- Promotion Queue ---

@router.get("/queue")
async def list_queue(status: str = "pending", limit: int = 50, db: AsyncSession = Depends(get_db)):
    """List promotion queue candidates."""
    if status == "pending":
        from rag.promotion import list_pending
        return {"candidates": await list_pending(db, limit)}
    else:
        result = await db.execute(
            select(PromotionCandidate, RagChunk)
            .join(RagChunk, PromotionCandidate.chunk_id == RagChunk.id)
            .where(PromotionCandidate.status == status)
            .order_by(PromotionCandidate.reviewed_at.desc())
            .limit(limit)
        )
        rows = result.all()
        return {"candidates": [{
            "candidate_id": str(c.id),
            "chunk_title": r.title,
            "trust_tier": r.trust_tier,
            "reason": c.reason,
            "okf_concept_ref": c.okf_concept_ref or "",
            "reviewed_at": str(c.reviewed_at) if c.reviewed_at else "",
        } for c, r in rows]}


class PromoteRequest(BaseModel):
    candidate_id: str
    action: str  # "approve" | "reject"
    title: Optional[str] = None
    description: Optional[str] = None
    body_override: Optional[str] = None


@router.post("/promote")
async def promote_chunk(req: PromoteRequest, db: AsyncSession = Depends(get_db)):
    """Approve or reject a promotion candidate.

    Approve: writes an OKF concept to the Oracle's knowledge bundle, marks the
    chunk as promoted_to_okf. This is the ONLY path from raw → curated.
    Reject: discards the candidate without writing.
    """
    if req.action == "approve":
        from rag.promotion import approve
        result = await approve(
            db,
            candidate_id=req.candidate_id,
            title=req.title,
            description=req.description,
            body_override=req.body_override,
        )
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    elif req.action == "reject":
        from rag.promotion import reject
        result = await reject(db, candidate_id=req.candidate_id)
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")


class FlagRequest(BaseModel):
    chunk_id: str
    reason: str = "manual"
    trigger_query: str = ""
    summary: str = ""


@router.post("/flag")
async def flag_chunk(req: FlagRequest, db: AsyncSession = Depends(get_db)):
    """Manually flag a chunk for promotion review."""
    from rag.promotion import create_candidate
    return await create_candidate(
        db,
        chunk_id=req.chunk_id,
        reason=req.reason,
        trigger_query=req.trigger_query,
        summary=req.summary,
    )
