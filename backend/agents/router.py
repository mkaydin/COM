from litellm import completion
from config import get_settings
from logging_config import get_logger
from agents.schemas import QueryClassification
from agents.prompts import ROUTER_SYSTEM_PROMPT

logger = get_logger(__name__)
settings = get_settings()


async def router_node(state: dict) -> dict:
    query = state.get("user_query", "")
    logger.info("router_classifying", query=query[:100])

    messages = [
        {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
        {"role": "user", "content": f"Classify this query:\n\n{query}"}
    ]

    try:
        response = completion(
            model=settings.llm_model,
            messages=messages,
            temperature=0.2,
            max_tokens=256,
        )
        raw = response.choices[0].message.content

        import json, re
        json_match = re.search(r'\{[\s\S]*\}', raw)
        data = json.loads(json_match.group(0)) if json_match else {}
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
