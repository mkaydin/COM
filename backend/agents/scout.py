from typing import Optional, List
from pydantic import BaseModel, Field
from litellm import completion
from config import get_settings
from logging_config import get_logger
from agents.schemas import ScoutResponse, SourceCitation
from agents.prompts import SCOUT_SYSTEM_PROMPT

logger = get_logger(__name__)
settings = get_settings()


async def scout_node(state: dict) -> dict:
    query = state.get("user_query", "")
    logger.info("scout_processing", query=query[:100])

    messages = [
        {"role": "system", "content": SCOUT_SYSTEM_PROMPT},
        {"role": "user", "content": f"Research this topic and produce an intelligence report with the ScoutResponse JSON schema:\n\n{query}"}
    ]

    try:
        response = completion(
            model=settings.llm_model,
            messages=messages,
            temperature=0.3,
            max_tokens=2048,
        )
        raw = response.choices[0].message.content

        import json, re
        json_match = re.search(r'\{[\s\S]*\}', raw)
        data = json.loads(json_match.group(0)) if json_match else {}
        scout_output = ScoutResponse(**data)
    except Exception as e:
        logger.error("scout_llm_failed", error=str(e))
        scout_output = ScoutResponse(
            confidence=0.0,
            topic=query[:100],
            key_signals=[],
            consensus="Unable to complete reconnaissance.",
            source_diversity="low",
            source_reliability="failed",
        )

    logger.info("scout_complete", confidence=scout_output.confidence)
    return {"scout_response": scout_output.model_dump()}
