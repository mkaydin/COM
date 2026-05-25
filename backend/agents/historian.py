import json, re
from litellm import completion
from config import get_settings
from logging_config import get_logger
from agents.schemas import HistorianResponse
from agents.prompts import HISTORIAN_SYSTEM_PROMPT

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
    if documents:
        context_block = "\n\nRETRIEVED DOCUMENTS:\n" + "\n".join(
            f"- [{d['doc_id'][:8]}...] {d['title']}: {d['excerpt'][:200]} (relevance: {d['relevance']:.2f})"
            for d in documents
        )
    else:
        context_block = "\n\nRETRIEVED DOCUMENTS: (none available — no documents in ChromaDB yet)"

    messages = [
        {"role": "system", "content": HISTORIAN_SYSTEM_PROMPT},
        {"role": "user", "content": f"Answer this query using the provided evidence sources. Output HistorianResponse JSON.\n\nQUERY: {query}{context_block}"}
    ]

    try:
        response = completion(
            model=settings.llm_model,
            messages=messages,
            temperature=0.3,
            max_tokens=2048,
        )
        raw = response.choices[0].message.content

        json_match = re.search(r'\{[\s\S]*\}', raw)
        data = json.loads(json_match.group(0)) if json_match else {}
        historian_output = HistorianResponse(**data)
    except Exception as e:
        logger.error("historian_llm_failed", error=str(e))
        historian_output = HistorianResponse(
            confidence=0.0,
            claims=[],
            verdict="Unable to retrieve evidence.",
            insufficient_data=True,
        )

    logger.info("historian_complete", confidence=historian_output.confidence)
    return {"historian_response": historian_output.model_dump()}
