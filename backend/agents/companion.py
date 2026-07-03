import json, re
from litellm import completion
from config import get_settings
from logging_config import get_logger
from agents.schemas import CompanionResponse
from agents.prompts import COMPANION_SYSTEM_PROMPT
from agents.json_utils import schema_for_prompt, extract_json

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
        {"role": "system", "content": COMPANION_SYSTEM_PROMPT + schema_for_prompt(CompanionResponse)},
        {"role": "user", "content": f"""The Council has deliberated on this query. Advise the user in plain language.

QUERY: {query}
QUERY TYPE: {query_type}
RECOMMENDED PRIORITY: {', '.join(priority) if priority else 'balanced'}

AGENT RESPONSES:
{agent_briefs}

MODERATOR SYNTHESIS:
{moderator_summary}

You MUST provide trust_guidance for ALL FOUR agents: scout, historian, detective, sage. Each entry MUST include the 'agent' field with the exact agent name. Be helpful, clear, and actionable."""}
    ]

    try:
        from config import llm_complete
        response = await llm_complete(messages, temperature=0.5, max_tokens=4096, provider=provider)
        raw = response.choices[0].message.content
        _reasoning = getattr(response, 'reasoning', '')
        data = extract_json(raw)

        known_agents = ["scout", "historian", "detective", "sage"]
        guidance = data.get("trust_guidance", [])
        for i, g in enumerate(guidance):
            if isinstance(g, dict) and not g.get("agent") and i < len(known_agents):
                g["agent"] = known_agents[i]

        if len(guidance) < 4:
            existing = {g.get("agent", "") for g in guidance if isinstance(g, dict)}
            for agent_name in known_agents:
                if agent_name not in existing:
                    guidance.append({
                        "agent": agent_name,
                        "trust_level": "medium",
                        "reason": "No specific guidance generated for this agent.",
                    })
            data["trust_guidance"] = guidance

        companion_output = CompanionResponse(**data)
    except Exception as e:
        _reasoning = ""
        logger.error("companion_llm_failed", error=str(e))
        companion_output = CompanionResponse(
            translation="The Council was unable to reach a clear conclusion.",
            summary="Deliberation failed.",
            recommended_agent="historian",
            recommendation_reason="Default recommendation due to processing error.",
        )

    logger.info("companion_complete", recommended=companion_output.recommended_agent)
    _result = companion_output.model_dump()
    _result["_reasoning"] = _reasoning
    return {"companion_notes": _result}
