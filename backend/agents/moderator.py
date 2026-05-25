import json, re
from litellm import completion
from config import get_settings
from logging_config import get_logger
from agents.schemas import ModeratorResponse
from agents.prompts import MODERATOR_SYSTEM_PROMPT

logger = get_logger(__name__)
settings = get_settings()


def _summarize_agent_response(name: str, response: dict | None, max_chars: int = 800) -> str:
    if not response:
        return f"{name}: no response"
    return json.dumps(response, default=str)[:max_chars]


async def moderator_node(state: dict) -> dict:
    logger.info("moderator_synthesizing")

    deliberation_id = state.get("deliberation_id", "")

    agent_summaries = "\n\n".join([
        f"=== SCOUT RESPONSE ===\n{_summarize_agent_response('scout', state.get('scout_response'))}",
        f"=== HISTORIAN RESPONSE ===\n{_summarize_agent_response('historian', state.get('historian_response'))}",
        f"=== DETECTIVE RESPONSE ===\n{_summarize_agent_response('detective', state.get('detective_response'))}",
        f"=== SAGE RESPONSE ===\n{_summarize_agent_response('sage', state.get('sage_response'))}",
    ])

    messages = [
        {"role": "system", "content": MODERATOR_SYSTEM_PROMPT},
        {"role": "user", "content": f"Compare these four agent responses, identify conflicts and agreements, and map the decision space. Output ModeratorResponse JSON.\n\nDELIBERATION ID: {deliberation_id}\n\n{agent_summaries}"}
    ]

    try:
        response = completion(
            model=settings.llm_model,
            messages=messages,
            temperature=0.2,
            max_tokens=3072,
        )
        raw = response.choices[0].message.content

        json_match = re.search(r'\{[\s\S]*\}', raw)
        data = json.loads(json_match.group(0)) if json_match else {}
        moderator_output = ModeratorResponse(**data)
    except Exception as e:
        logger.error("moderator_llm_failed", error=str(e))
        moderator_output = ModeratorResponse(
            deliberation_id=deliberation_id,
            comparison={},
            conflicts=[],
            agreements=[],
            decision_space=[],
        )

    conflicts = moderator_output.model_dump().get("conflicts", [])
    unresolved = [c for c in conflicts if c.get("status") != "resolved"]
    should_critique = len(unresolved) > 0

    logger.info("moderator_complete", conflicts=len(conflicts), unresolved=len(unresolved))
    return {
        "moderator_synthesis": moderator_output.model_dump(),
        "should_critique": should_critique,
        "unresolved_conflicts": unresolved,
    }
