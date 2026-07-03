import json, re
from litellm import completion
from config import get_settings
from logging_config import get_logger
from agents.schemas import HistorianResponse
from agents.prompts import HISTORIAN_SYSTEM_PROMPT
from agents.json_utils import schema_for_prompt, extract_json

logger = get_logger(__name__)
settings = get_settings()


async def _retrieve_documents(query: str, top_k: int = 5) -> list[dict]:
    try:
        import chromadb
        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        try:
            collection = client.get_collection("documents_shared")
        except Exception:
            client.create_collection("documents_shared")
            collection = client.get_collection("documents_shared")

        results = collection.query(
            query_texts=[query],
            n_results=top_k,
        )
        documents = []
        if results and results.get("metadatas") and results["metadatas"][0]:
            for i, meta in enumerate(results["metadatas"][0]):
                doc_text = results["documents"][0][i] if results.get("documents") else ""
                documents.append({
                    "doc_id": meta.get("doc_id", ""),
                    "title": meta.get("title", "Untitled"),
                    "excerpt": doc_text[:300] if doc_text else "",
                    "relevance": results.get("distances", [[1.0]])[0][i] if results.get("distances") else 0.0,
                })
        return documents
    except Exception as e:
        logger.warning("vector_retrieval_unavailable", error=str(e))
        return []


async def historian_node(state: dict) -> dict:
    query = state.get("user_query", "")
    logger.info("historian_processing", query=query[:100])

    documents = await _retrieve_documents(query)

    context_block = ""
    citation_map = {}
    if documents:
        context_block = "\n\nRETRIEVED EVIDENCE (cite each source by its [E#] ID in your claims):\n"
        for i, d in enumerate(documents):
            eid = f"E{i+1}"
            citation_map[eid] = {"doc_id": d["doc_id"], "title": d["title"], "relevance": d["relevance"]}
            context_block += f"[{eid}] {d['title']} (relevance: {d['relevance']:.2f})\n    \"{d['excerpt'][:300]}\"\n"
    else:
        context_block = "\n\nRETRIEVED EVIDENCE: (none available — no documents in library)"

    messages = [
        {"role": "system", "content": HISTORIAN_SYSTEM_PROMPT + schema_for_prompt(HistorianResponse)},
        {"role": "user", "content": f"Answer this query using ONLY the provided evidence. Each claim MUST include a 'sources' list referencing the evidence IDs (e.g. [\"E1\", \"E3\"]). If no evidence is available, set insufficient_data=true.\n\nQUERY: {query}{context_block}"}
    ]

    try:
        from config import llm_complete
        response = await llm_complete(messages, temperature=0.3, max_tokens=4096, provider=provider)
        raw = response.choices[0].message.content
        _reasoning = getattr(response, 'reasoning', '')
        data = extract_json(raw)

        for claim in data.get("claims", []):
            srcs = claim.get("sources", [])
            if isinstance(srcs, list):
                claim["sources"] = [
                    citation_map.get(s, {"title": s}) if isinstance(s, str) else s
                    for s in srcs
                ]

        historian_output = HistorianResponse(**data)
    except Exception as e:
        _reasoning = ""
        logger.error("historian_llm_failed", error=str(e))
        historian_output = HistorianResponse(
            confidence=0.0,
            claims=[],
            verdict="Unable to retrieve evidence.",
            insufficient_data=True,
        )

    logger.info("historian_complete", confidence=historian_output.confidence)
    _result = historian_output.model_dump()
    _result["_reasoning"] = _reasoning
    return {"historian_response": _result}
