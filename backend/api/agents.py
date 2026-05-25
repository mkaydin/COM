from fastapi import APIRouter
from agents.prompts import AGENT_CONFIGS

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
async def list_agents():
    return [
        {
            "name": name,
            "temperature": config["temperature"],
            "max_tokens": config["max_tokens"],
        }
        for name, config in AGENT_CONFIGS.items()
    ]


@router.get("/{name}/stats")
async def get_agent_stats(name: str):
    return {
        "agent": name,
        "deliberations_count": 0,
        "avg_confidence": None,
        "avg_latency_ms": None,
        "total_tokens": 0,
    }
