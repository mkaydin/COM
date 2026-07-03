from litellm import completion
from config import get_settings
from logging_config import get_logger
from agents.schemas import QueryClassification
from agents.prompts import ROUTER_SYSTEM_PROMPT
from agents.json_utils import extract_json

logger = get_logger(__name__)
settings = get_settings()


async def router_node(state: dict) -> dict:
    query = state.get("user_query", "")
    provider = state.get("provider", "lm_studio")
    logger.info("router_classifying", query=query[:100])

    if provider == "lm_studio":
        from agents.prompts import LOCAL_ROUTER_PROMPT
        system_content = LOCAL_ROUTER_PROMPT
        max_tok = 512
    else:
        system_content = ROUTER_SYSTEM_PROMPT
        max_tok = 4096

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"Classify this query:\n\n{query}"}
    ]

    try:
        from config import llm_complete
        response = await llm_complete(messages, temperature=0.2, max_tokens=max_tok, provider=provider)
        raw = response.choices[0].message.content
        data = extract_json(raw)
        classification = QueryClassification(**data)
    except Exception as e:
        logger.error("router_failed", error=str(e))
        classification = QueryClassification(
            query_type="general",
            confidence=0.5,
            reasoning="Classification failed, defaulting to general"
        )

    logger.info("router_result", query_type=classification.query_type)

    priority_map = {
        "factual": ["historian", "scout", "detective", "sage"],
        "relational": ["detective", "historian", "scout", "sage"],
        "strategic": ["sage", "detective", "historian", "scout"],
        "general": ["scout", "historian", "detective", "sage"],
    }

    return {
        "query_type": classification.query_type,
        "agent_priority": priority_map.get(classification.query_type, priority_map["general"]),
    }
