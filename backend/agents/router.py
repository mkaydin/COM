from config import get_settings
from logging_config import get_logger
from agents.schemas import QueryClassification
from agents.prompts import LOCAL_ROUTER_PROMPT
from agents.json_utils import extract_json

logger = get_logger(__name__)
settings = get_settings()

# Small local models need a terse system prompt and a tight token budget.
MAX_TOKENS = 512


async def router_node(state: dict) -> dict:
    query = state.get("user_query", "")
    logger.info("router_classifying", query=query[:100])

    messages = [
        {"role": "system", "content": LOCAL_ROUTER_PROMPT},
        {"role": "user", "content": f"Classify this query:\n\n{query}"},
    ]

    try:
        from config import llm_complete
        response = await llm_complete(messages, role="MODERATOR", temperature=0.2, max_tokens=MAX_TOKENS)
        raw = response.content
        data = extract_json(raw)
        classification = QueryClassification(**data)
    except Exception as e:
        logger.error("router_failed", error=str(e))
        classification = QueryClassification(
            query_type="general",
            confidence=0.5,
            reasoning="Classification failed, defaulting to general",
        )

    logger.info("router_result", query_type=classification.query_type)
    return {"query_type": classification.query_type}
