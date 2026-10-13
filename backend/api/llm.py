"""LLM provider + per-role model management.

The forum runs five characters, each on its own model. This is where the user
sees that mapping and changes it:

    GET  /api/llm/providers       every provider, whether it is configured
    GET  /api/llm/roles           the live role -> model mapping
    POST /api/llm/roles           pin a role to a model (persists)
    DELETE /api/llm/roles/{role}  unpin, back to env/default
    GET  /api/llm/models          the free catalogue + installed local models
    POST /api/llm/test            send a real completion to check a model
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from llm import catalogue, health, list_models, providers, roles, test_model
from agents import registry
from logging_config import get_logger

router = APIRouter(prefix="/api/llm", tags=["llm"])
logger = get_logger(__name__)


@router.get("/providers")
async def list_providers():
    out = []
    for pid in providers.available():
        try:
            out.append(providers.describe(providers.get(pid)))
        except Exception as e:
            out.append({"id": pid, "error": str(e)[:200]})
    return {"providers": out, "active": settings_provider()}


def settings_provider() -> str:
    from config import get_settings

    return (get_settings().llm_provider or "opencode").lower()


@router.get("/roles")
async def list_roles():
    """The live role -> model mapping, with where each value came from."""
    try:
        provider = providers.get()
    except providers.ProviderError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    entries = roles.all_roles(provider)
    # Attach the character each role belongs to, so the UI can label them.
    by_role = {}
    for c in registry.all_characters():
        by_role[c.model_role] = c

    for e in entries:
        c = by_role.get(e["role"])
        e["character"] = c.key if c else None
        e["character_name"] = c.display_name if c else None

    return {
        "provider": provider.id,
        "base_url": provider.base_url,
        "roles": entries,
        "free_defaults": catalogue.OPENCODE_ROLE_DEFAULTS,
    }


class RoleSet(BaseModel):
    role: str
    model_id: str = Field(..., min_length=1, max_length=255)


@router.post("/roles")
async def set_role(body: RoleSet):
    """Pin a role to a model. Persists to `llm_roles` and takes effect at once."""
    try:
        return await roles.set_override(body.role, body.model_id)
    except providers.ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/roles/{role}")
async def clear_role(role: str):
    try:
        return await roles.clear_override(role)
    except providers.ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/models")
async def models(provider: Optional[str] = None):
    """Free catalogue + (for ollama) the installed tags."""
    try:
        return await list_models(provider)
    except providers.ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class TestRequest(BaseModel):
    model_id: str
    provider: Optional[str] = None


@router.post("/test")
async def test(body: TestRequest):
    """Send a real completion to confirm a model answers.

    Exists because "the provider has a key" and "this model actually works"
    are different claims, and only the second one matters.
    """
    return await test_model(body.model_id, body.provider)


@router.post("/reload")
async def reload_overrides():
    """Re-read persisted role overrides (after editing .env, for instance)."""
    loaded = await roles.load_overrides()
    return {"loaded": loaded, "count": len(loaded)}