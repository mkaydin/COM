import json, re
from litellm import completion
from config import get_settings
from logging_config import get_logger
from agents.schemas import SageResponse
from agents.prompts import SAGE_SYSTEM_PROMPT
from agents.sage_wiki import search_wiki, load_index

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

    messages = [
        {"role": "system", "content": SAGE_SYSTEM_PROMPT},
        {"role": "user", "content": f"Synthesize an answer for this query using the wiki knowledge below. Output SageResponse JSON with at least one cross-domain analogy.\n\nQUERY: {query}\n\n{wiki_context}"}
    ]

    try:
        response = completion(
            model=settings.llm_model,
            messages=messages,
            temperature=0.7,
            max_tokens=2048,
        )
        raw = response.choices[0].message.content

        json_match = re.search(r'\{[\s\S]*\}', raw)
        data = json.loads(json_match.group(0)) if json_match else {}
        sage_output = SageResponse(**data)
    except Exception as e:
        logger.error("sage_llm_failed", error=str(e))
        sage_output = SageResponse(
            confidence=0.0,
            synthesis="Unable to complete synthesis.",
            speculation_warning=True,
        )

    logger.info("sage_complete", confidence=sage_output.confidence)
    return {"sage_response": sage_output.model_dump()}
