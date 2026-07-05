"""
RAG Agent — the raw/growing knowledge specialist for the Council of Minds.

Answers queries directly using the unified retrieval layer (spec §4):
  1. Retrieve from OKF (curated) → Wiki (curated) → rag_chunks (raw, vector).
  2. Merge + rank by trust_tier.
  3. LLM synthesizes an answer citing source_url + trust_tier for every claim.

The RAG agent is the institutional memory of raw evidence — it surfaces the
primary-source PDFs, scraped pages, and search results that the curated systems
(OKF/Wiki) haven't yet absorbed. Each answer carries explicit citations so the
user can trace claims back to their source and assess reliability.
"""
import asyncio
from config import get_settings, llm_complete
from logging_config import get_logger
from agents.schemas import RagResponse, RagCitation
from agents.json_utils import schema_for_prompt, extract_json

logger = get_logger(__name__)
settings = get_settings()


async def rag_node(state: dict) -> dict:
    query = state.get("user_query", "")
    provider = state.get("provider", "ollama")
    logger.info("rag_processing", query=query[:80])

    # 1. Unified retrieval (OKF → Wiki → vector fallback).
    retrieval_result = None
    try:
        from db.postgres import async_session
        from rag.retrieval import retrieve
        async with async_session() as db:
            retrieval_result = await retrieve(db, query, top_k=10)
    except Exception as e:
        logger.error("rag_retrieval_failed", error=str(e)[:120])

    # 2. Build context + citations from results.
    if retrieval_result is None:
        context = "(Retrieval system unavailable.)"
        citations_raw = []
        stats = {"raw_count": 0, "curated_count": 0, "gap_detected": False}
    else:
        context = retrieval_result.get("context", "(No results.)")
        stats = retrieval_result.get("stats", {})
        all_results = retrieval_result.get("all", [])
        citations_raw = _build_citations(all_results)

    # 3. Select prompt + call LLM.
    if provider == "ollama":
        system_prompt = LOCAL_RAG_PROMPT
        max_tok = 8192
    else:
        system_prompt = RAG_SYSTEM_PROMPT + schema_for_prompt(RagResponse)
        max_tok = 8192

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            f"QUERY: {query}\n\n"
            f"RETRIEVED KNOWLEDGE:\n{context}"
        )},
    ]

    try:
        response = await llm_complete(messages, temperature=0.3, max_tokens=max_tok, provider=provider)
        raw = response.choices[0].message.content
        _reasoning = getattr(response, "reasoning", "")
        data = extract_json(raw)

        if data.get("confidence", 0) > 1:
            data["confidence"] = data["confidence"] / 100.0

        # If the model didn't populate citations, inject them from retrieval.
        if not data.get("citations"):
            data["citations"] = [c.model_dump() for c in citations_raw]

        # Flag insufficient data if no results were retrieved.
        if stats.get("raw_count", 0) == 0 and stats.get("curated_count", 0) == 0:
            data.setdefault("insufficient_data", True)

        rag_output = RagResponse(**data)
    except Exception as e:
        _reasoning = ""
        logger.error("rag_llm_failed", error=str(e))
        rag_output = RagResponse(
            confidence=0.0,
            answer="Unable to retrieve or synthesize knowledge for this query.",
            insufficient_data=True,
            citations=citations_raw[:5],
        )

    logger.info(
        "rag_complete",
        confidence=rag_output.confidence,
        citations=len(rag_output.citations),
        curated=stats.get("curated_count", 0),
        raw=stats.get("raw_count", 0),
        gap=stats.get("gap_detected", False),
    )

    _result = rag_output.model_dump()
    _result["_reasoning"] = _reasoning
    _result["_rag_stats"] = stats
    return {"rag_response": _result}


def _build_citations(results: list[dict]) -> list[RagCitation]:
    """Extract citation objects from retrieval results (deduplicated, top 10)."""
    seen: set[str] = set()
    citations: list[RagCitation] = []
    for r in results:
        key = r.get("id") or r.get("title") or ""
        if key in seen:
            continue
        seen.add(key)
        snippet = (r.get("text") or r.get("content") or "")[:200]
        citations.append(RagCitation(
            title=r.get("title", "")[:200],
            source_url=r.get("source_url") or r.get("resource") or "",
            trust_tier=r.get("trust_tier", ""),
            source_system=r.get("source_system", ""),
            snippet=snippet,
        ))
        if len(citations) >= 10:
            break
    return citations


# === Prompts ===

RAG_SYSTEM_PROMPT = """You are the RAG agent — the raw evidence specialist for the Council of Minds.

Your job: answer the query using the retrieved knowledge, citing source_url and
trust_tier for every factual claim. You are the agent that goes deep into
primary sources — PDFs, scraped pages, and search results.

CORE PHILOSOPHY:
- Evidence first. Every claim must trace back to a retrieved source.
- Trust matters. A peer_reviewed paper carries more weight than an ai_summary.
  Always tell the user which tier your evidence comes from.
- Gaps are data. If the retrieval didn't cover an aspect of the query, say so.

OUTPUT RULES:
- Write a thorough answer using ## headings and bullet points.
- For each major claim, cite inline: [Source: title (trust_tier)].
- List all sources in the citations array with their trust_tier.
- Set insufficient_data=true when retrieval has no relevant coverage.
- Set gaps to list aspects of the query the retrieval couldn't cover.

OUTPUT FORMAT: Structured JSON matching RagResponse schema.
"""


LOCAL_RAG_PROMPT = """You are the RAG agent. Answer using the retrieved knowledge. Cite sources.

Distinguish trust tiers: peer_reviewed > preprint > web_primary > web_secondary > ai_summary.

Respond with ONLY this JSON:
{
  "confidence": 0.0 to 1.0,
  "reasoning_type": "evidence_retrieval",
  "answer": "your detailed answer with ## headings, bullet points, and inline [Source: title (trust_tier)] citations",
  "citations": [{"title": "...", "source_url": "...", "trust_tier": "...", "source_system": "...", "snippet": "..."}],
  "gaps": ["aspects not covered by retrieval"],
  "trust_summary": "e.g. 'Based on 3 preprint papers and 1 ai_summary'",
  "insufficient_data": false
}
"""
