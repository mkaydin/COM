from fastapi import APIRouter
from agents import registry
from logging_config import get_logger

router = APIRouter(prefix="/api/agents", tags=["agents"])
logger = get_logger(__name__)


# The Moderator is not a forum character — it opens and closes topics rather
# than holding a position — but it is still an agent for profile/stats
# purposes, so it is appended here rather than living in the character
# registry.
MODERATOR_CARD = {
    "name": "moderator",
    "display_name": "The Moderator",
    "archetype": "Master of Rules",
    "color": "mystic-purple",
    "icon": "gavel",
    "temperature": 0.25,
    "model_role": "MODERATOR",
    "knowledge": [],
    "voice_rules": [
        "Opens the topic and frames the question.",
        "Reports what the Council actually concluded, not what sounds decisive.",
        "Cannot declare consensus the thread has not demonstrably reached.",
    ],
    "description": (
        "Opens topics, invites the cast, and closes the thread. Publishes one "
        "accepted answer when the Council converges, or several attributed "
        "positions when it does not."
    ),
    "tags": ["Synthesizing", "Impartial", "Structuring"],
}


def _agent_cards() -> list[dict]:
    """Every agent card, with each character's live model binding.

    Built from the registry rather than a literal list, so a new character
    appears here automatically.
    """
    from llm import providers, roles

    try:
        provider = providers.get()
        bindings = {r["role"]: r["model"] for r in roles.all_roles(provider)}
    except Exception:
        bindings = {}

    out = []
    for c in registry.all_characters():
        card = c.brief()
        card["reasoning_type"] = (
            "evidence" if ("okf" in c.knowledge or "rag" in c.knowledge) else "synthesis"
        )
        card["data_source"] = " + ".join(c.knowledge) or "internal reasoning"
        card["model"] = bindings.get(c.model_role)
        out.append(card)

    mod = dict(MODERATOR_CARD)
    mod["reasoning_type"] = "synthesis"
    mod["data_source"] = "the whole thread (arbiter, no external data)"
    mod["model"] = bindings.get("MODERATOR")
    out.append(mod)
    return out


@router.get("")
async def list_agents():
    return _agent_cards()


@router.get("/{name}")
async def get_agent(name: str):
    for agent in _agent_cards():
        if agent["name"] == name:
            return agent
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{name}/stats")
async def get_agent_stats(name: str):
    from db.postgres import async_session
    from sqlalchemy import select, func
    from sqlalchemy.sql import func as sqlfunc
    from db.models import AgentResponse, Critique
    import math

    async with async_session() as db:
        result = await db.execute(
            select(
                func.count(AgentResponse.id).label("total_responses"),
                func.avg(AgentResponse.confidence).label("avg_confidence"),
                func.avg(AgentResponse.latency_ms).label("avg_latency_ms"),
                func.sum(AgentResponse.token_count).label("total_tokens"),
            ).where(AgentResponse.agent_name == name)
        )
        row = result.one()

        critique_count = await db.execute(
            select(func.count(Critique.id))
            .where(
                (Critique.source_agent == name) | (Critique.target_agent == name)
            )
        )
        total_critiques = critique_count.scalar() or 0

        latency_rows = await db.execute(
            select(AgentResponse.latency_ms)
            .where(AgentResponse.agent_name == name)
            .where(AgentResponse.latency_ms.isnot(None))
            .order_by(AgentResponse.latency_ms)
        )
        latencies = [r[0] for r in latency_rows.fetchall()]
        latency_p50 = None
        latency_p95 = None
        if latencies:
            latencies.sort()
            n = len(latencies)
            latency_p50 = float(latencies[math.floor(n * 0.5)])
            latency_p95 = float(latencies[math.floor(n * 0.95)])

        total_tokens = int(row.total_tokens) if row.total_tokens else None

        return {
            "agent": name,
            "total_responses": row.total_responses or 0,
            "avg_confidence": round(float(row.avg_confidence), 3) if row.avg_confidence else None,
            "avg_latency_ms": round(float(row.avg_latency_ms), 1) if row.avg_latency_ms else None,
            "latency_p50_ms": latency_p50,
            "latency_p95_ms": latency_p95,
            "total_tokens": total_tokens,
            "total_critiques": total_critiques,
        }
