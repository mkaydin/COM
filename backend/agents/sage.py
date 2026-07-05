import json, re
from litellm import completion
from config import get_settings
from logging_config import get_logger
from agents.schemas import SageResponse
from agents.prompts import SAGE_SYSTEM_PROMPT
from agents.sage_wiki import search_wiki, load_index
from agents.json_utils import schema_for_prompt, extract_json

logger = get_logger(__name__)
settings = get_settings()


async def sage_node(state: dict) -> dict:
    query = state.get("user_query", "")
    logger.info("sage_processing", query=query[:100])

    wiki_pages = search_wiki(query, max_pages=5)
    index_content = load_index()

    wiki_context = f"WIKI INDEX:\n{index_content[:2000]}\n\n"
    if wiki_pages:
        wiki_context += "RELEVANT WIKI PAGES:\n" + "\n---\n".join(
            f"[[{p['path']}]] {p['title']}\n{p['content'][:1500]}"
            for p in wiki_pages
        )
    else:
        wiki_context += "No matching wiki pages found."

    # RAG supporter: inject raw knowledge chunks (trust-tier annotated) alongside wiki.
    rag_context = ""
    rag_chunk_count = 0
    try:
        from db.postgres import async_session
        from rag.retrieval import retrieve_raw_context
        async with async_session() as db:
            rag_context = await retrieve_raw_context(db, query, top_k=5)
            rag_chunk_count = rag_context.count("[")  # rough count of annotated entries
        if rag_context:
            logger.info("sage_rag_supporter", rag_chunks=rag_chunk_count)
    except Exception as e:
        logger.warning("sage_rag_supporter_failed", error=str(e)[:100])

    full_context = wiki_context
    if rag_context:
        full_context += "\n\n" + rag_context

    # Select prompt based on provider
    provider = state.get("provider", "ollama")
    if provider == "ollama":
        from agents.prompts import LOCAL_SAGE_PROMPT
        system_content = LOCAL_SAGE_PROMPT
        max_tok = 4096
    else:
        system_content = SAGE_SYSTEM_PROMPT + schema_for_prompt(SageResponse)
        max_tok = 4096

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"Synthesize an answer.\n\nQUERY: {query}\n\n{full_context}"}
    ]

    try:
        from config import llm_complete
        response = await llm_complete(messages, temperature=0.7, max_tokens=max_tok, provider=provider)
        raw = response.choices[0].message.content
        _reasoning = getattr(response, 'reasoning', '')
        data = extract_json(raw)
        if data.get("confidence", 0) > 1: data["confidence"] = data["confidence"] / 100.0
        sage_output = SageResponse(**data)
    except Exception as e:
        _reasoning = ""
        logger.error("sage_llm_failed", error=str(e))
        sage_output = SageResponse(
            confidence=0.0,
            synthesis="Unable to complete synthesis.",
            speculation_warning=True,
        )

    logger.info("sage_complete", confidence=sage_output.confidence)

    if sage_output.confidence >= 0.6 and sage_output.synthesis and len(sage_output.synthesis) > 50:
        try:
            from agents.sage_wiki import file_insight
            await file_insight(query, sage_output.model_dump())
            logger.info("sage_insight_filed", query=query[:50])
        except Exception as e:
            logger.warning("sage_file_insight_failed", error=str(e))

    _result = sage_output.model_dump()
    _result["_reasoning"] = _reasoning
    return {"sage_response": _result}
