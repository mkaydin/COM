from fastapi import APIRouter
from logging_config import get_logger

router = APIRouter(prefix="/api/agents", tags=["agents"])
logger = get_logger(__name__)


AGENTS = [
    {
        "name": "oracle",
        "display_name": "The Oracle",
        "archetype": "Seeker of Truth",
        "color": "runed-gold",
        "icon": "auto_awesome",
        "temperature": 0.3,
        "reasoning_type": "evidence",
        "data_source": "Structured Knowledge Bundle (OKF)",
        "description": "Grounds every answer in the structured knowledge bundle. Every claim must be backed by cited evidence, and when evidence is thin it says so explicitly rather than guessing.",
        "tags": ["Evidence-Bound", "Skeptical", "Honest About Uncertainty"],
    },
    {
        "name": "rag",
        "display_name": "The Scholar",
        "archetype": "Keeper of Lore",
        "color": "success",
        "icon": "library_books",
        "temperature": 0.3,
        "reasoning_type": "evidence",
        "data_source": "Vector RAG (pgvector + ChromaDB + document archive)",
        "description": "Retrieves verifiable knowledge from documents and the archive, ranking every source by a trust tier. Answers from what can be backed up and flags gaps where the record runs out.",
        "tags": ["Source-Ranked", "Verifiable", "Gap-Aware"],
    },
    {
        "name": "sage",
        "display_name": "The Sage",
        "archetype": "Voice of Intuition",
        "color": "ember-glow",
        "icon": "local_fire_department",
        "temperature": 0.7,
        "reasoning_type": "synthesis",
        "data_source": "LLM Wiki (persistent markdown) + internal reasoning",
        "description": "Approaches deliberation through wisdom and moral philosophy, weighing the deeper implications, human factors, and long-term consequences. Challenges assumptions and questions underlying values.",
        "tags": ["Philosophical", "Ethical", "Holistic"],
    },
    {
        "name": "moderator",
        "display_name": "The Moderator",
        "archetype": "Master of Rules",
        "color": "mystic-purple",
        "icon": "gavel",
        "temperature": 0.2,
        "reasoning_type": "synthesis",
        "data_source": "All agent responses (arbiter, no external data)",
        "description": "Synthesizes all council responses into a structured decision space — agreements, conflicts, and tradeoffs. Does NOT decide; that stays with the user.",
        "tags": ["Synthesizing", "Impartial", "Structuring"],
    },
]


@router.get("")
async def list_agents():
    return AGENTS


@router.get("/{name}")
async def get_agent(name: str):
    for agent in AGENTS:
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
