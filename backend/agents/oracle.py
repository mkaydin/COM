"""
Oracle Agent — OKF knowledge custodian for the Council of Minds.

The Oracle reads the Open Knowledge Format (OKF) bundle — a directory tree of
markdown concepts with YAML frontmatter — using progressive disclosure + semantic
retrieval, then synthesizes an answer grounded in filed knowledge. Each finished
deliberation is filed back into the bundle as a new concept (see file_deliberation),
so the Oracle's knowledge compounds over time — exactly like Sage's wiki, but in
the spec'd, augmentation-guarded OKF format.

Pipeline:
  1. Retrieve the top-k concepts from the bundle most relevant to the query
     (sentence-transformer cosine similarity over title + description + tags).
  2. Load their bodies into context (progressive disclosure).
  3. LLM synthesis: evidence-backed claims, citing concept ids, with explicit
     confidence and an insufficient_data flag when the bundle has no coverage.
"""
import asyncio
from config import get_settings, llm_complete
from logging_config import get_logger
from agents.schemas import OracleResponse, OracleClaim
from agents.json_utils import extract_json

logger = get_logger(__name__)
settings = get_settings()


async def _retrieve_from_bundle(query: str, top_k: int = 5):
    """Retrieve relevant OKF concepts. Runs the (CPU-bound) encoder in a thread."""
    from okf.bundle import retrieve_relevant
    return await asyncio.to_thread(retrieve_relevant, query, None, top_k)


async def oracle_node(state: dict) -> dict:
    query = state.get("user_query", "")
    logger.info("oracle_processing", query=query[:80])

    # 1. Retrieve relevant concepts from the OKF bundle.
    concepts = await _retrieve_from_bundle(query, top_k=5)
    concept_ids = [c["id"] for c in concepts]
    logger.info("oracle_retrieved", concepts=len(concepts), ids=concept_ids)

    # 2. Build context.
    from okf.bundle import format_context
    context = await asyncio.to_thread(format_context, concepts)

    # RAG supporter: inject raw knowledge chunks (trust-tier annotated) alongside OKF.
    rag_context = ""
    try:
        from db.postgres import async_session
        from rag.retrieval import retrieve_raw_context
        async with async_session() as db:
            rag_context = await retrieve_raw_context(db, query, top_k=5)
        if rag_context:
            logger.info("oracle_rag_supporter", rag_chunks=rag_context.count("["))
    except Exception as e:
        logger.warning("oracle_rag_supporter_failed", error=str(e)[:100])

    full_context = context
    if rag_context:
        full_context += "\n\n" + rag_context

    # 3. Call LLM.
    system_prompt = LOCAL_ORACLE_PROMPT
    max_tok = 8192

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            f"QUERY: {query}\n\n"
            f"KNOWLEDGE BUNDLE (OKF concepts + RAG raw chunks):\n{full_context}"
        )},
    ]

    try:
        response = await llm_complete(messages, temperature=0.4, max_tokens=max_tok)
        raw = response.choices[0].message.content
        _reasoning = getattr(response, "reasoning", "")
        data = extract_json(raw)

        # Attach provenance: how many concepts grounded this answer.
        data["graph_entities_found"] = len(concepts)
        if not concepts:
            data.setdefault("insufficient_data", True)

        if data.get("confidence", 0) > 1:
            data["confidence"] = data["confidence"] / 100.0
        for cc in data.get("causal_chains", []):
            if cc.get("evidence_strength", 0) > 1:
                cc["evidence_strength"] = cc["evidence_strength"] / 100.0
        oracle_output = OracleResponse(**data)
    except Exception as e:
        _reasoning = ""
        logger.error("oracle_llm_failed", error=str(e))
        oracle_output = OracleResponse(
            confidence=0.0,
            claims=[],
            verdict="Unable to complete knowledge synthesis from the bundle.",
            insufficient_data=True if not concepts else False,
        )

    logger.info(
        "oracle_complete",
        confidence=oracle_output.confidence,
        claims=len(oracle_output.claims),
        grounded_in=len(concepts),
    )

    _result = oracle_output.model_dump()
    _result["_reasoning"] = _reasoning
    _result["_okf_concepts"] = concept_ids
    return {"oracle_response": _result}


# === Prompts ===

LOCAL_ORACLE_PROMPT = """You are the Oracle. Read the knowledge bundle concepts and respond with JSON.

Ground your answer in the retrieved concepts. Cite concept ids in sources.

Respond with ONLY this JSON:
{
  "confidence": 0.0 to 1.0,
  "reasoning_type": "okf_knowledge_synthesis",
  "claims": [{"text": "finding", "sources": ["deliberations/slug"], "evidence_type": "documented"}],
  "causal_chains": [{"chain": ["A", "B", "C"], "evidence_strength": 0.8, "is_inferred": false}],
  "hidden_links": [{"entity_a": "X", "entity_b": "Y", "relation": "relates_to"}],
  "verdict": "your main conclusion grounded in the bundle",
  "uncertainties": ["what the bundle does not cover"],
  "insufficient_data": false
}
"""
