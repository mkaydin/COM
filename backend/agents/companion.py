import json, re
from litellm import completion
from config import get_settings
from logging_config import get_logger
from agents.schemas import CompanionResponse
from agents.prompts import COMPANION_SYSTEM_PROMPT

logger = get_logger(__name__)
settings = get_settings()


def _brief(name: str, response: dict | None) -> str:
    if not response:
        return f"{name}: no response"
    conf = response.get("confidence", "?")
    verdict_or_synth = response.get("verdict") or response.get("synthesis") or response.get("consensus") or ""
    return f"{name} (confidence {conf}): {str(verdict_or_synth)[:300]}"


async def companion_node(state: dict) -> dict:
    logger.info("companion_advising")

    query = state.get("user_query", "")
    query_type = state.get("query_type", "general")
    deliberation_id = state.get("deliberation_id", "")

    moderator_summary = json.dumps(state.get("moderator_synthesis", {}), default=str)[:1500]

    agent_briefs = "\n".join([
        _brief("scout", state.get("scout_response")),
        _brief("historian", state.get("historian_response")),
        _brief("detective", state.get("detective_response")),
        _brief("sage", state.get("sage_response")),
    ])

    priority = state.get("agent_priority", [])

    messages = [
        {"role": "system", "content": COMPANION_SYSTEM_PROMPT},
        {"role": "user", "content": f"""The Council has deliberated on this query. Advise the user in plain language.

QUERY: {query}
QUERY TYPE: {query_type}
RECOMMENDED PRIORITY: {', '.join(priority) if priority else 'balanced'}

AGENT RESPONSES:
{agent_briefs}

MODERATOR SYNTHESIS:
{moderator_summary}

Output CompanionResponse JSON. Be helpful, clear, and actionable."""}
    ]

    try:
        response = completion(
            model=settings.llm_model,
            messages=messages,
            temperature=0.5,
            max_tokens=2048,
        )
        raw = response.choices[0].message.content

        json_match = re.search(r'\{[\s\S]*\}', raw)
        data = json.loads(json_match.group(0)) if json_match else {}
        companion_output = CompanionResponse(**data)
    except Exception as e:
        logger.error("companion_llm_failed", error=str(e))
        companion_output = CompanionResponse(
            translation="The Council was unable to reach a clear conclusion.",
            summary="Deliberation failed.",
            recommended_agent="historian",
            recommendation_reason="Default recommendation due to processing error.",
        )

    logger.info("companion_complete", recommended=companion_output.recommended_agent)
    return {"companion_notes": companion_output.model_dump()}
