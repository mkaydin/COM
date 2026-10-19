"""LLM provider + per-role model management.

The forum runs five characters, each on its own model. This is where the user
sees that mapping and changes it:

    GET  /api/llm/providers       every provider, whether it is configured
    GET  /api/llm/roles           the live role -> (provider, model) mapping
    POST /api/llm/roles           pin a role to a provider + model (persists)
    DELETE /api/llm/roles/{role}  unpin, back to env/default
    GET  /api/llm/models          the free catalogue + installed local models
    GET  /api/llm/discovery       live listing for every provider, chat + embedding
    GET  /api/llm/embedding       the selected embedding backend/model
    POST /api/llm/embedding       choose the embedding backend/model
    POST /api/llm/embedding/test  embed a probe and report the vector width
    POST /api/llm/test            send a real completion to check a model

Model selection is per character: each role carries both a provider and a model,
so the Oracle can run on a local Ollama model while the Moderator uses a free
Zen one. Embeddings are selected separately, because they are a single global
choice shared by retrieval, the OKF bundle and ingestion.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from llm import (
    catalogue,
    diagnose_call_error,
    discovery,
    embeddings,
    health,
    list_models,
    providers,
    roles,
    test_model,
)
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

    return (get_settings().llm_provider or "opencode-cli").lower()


@router.get("/roles")
async def list_roles():
    """The live role -> (provider, model) mapping, with where each came from.

    Each role resolves its own provider, so this passes none: pinning the global
    one here would report every character as being on it and hide a pinned
    override entirely.
    """
    try:
        # Still resolves the global provider, to surface a bad `LLM_PROVIDER`
        # as a 503 rather than an empty list.
        provider = providers.get()
    except providers.ProviderError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    entries = roles.all_roles()
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
    #: Which provider the model belongs to. Omit to pin the model but leave the
    #: role on the globally active provider — the behaviour before per-character
    #: provider selection existed.
    provider: Optional[str] = Field(default=None, max_length=64)


@router.post("/roles")
async def set_role(body: RoleSet):
    """Pin a role to a (provider, model). Persists to `llm_roles`, effective at once."""
    try:
        return await roles.set_override(body.role, body.model_id, body.provider)
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


@router.get("/discovery")
async def discover_models(provider: Optional[str] = None):
    """Every model each provider can serve right now, for the picker.

    Unlike `/api/llm/models`, this is the **live** listing: `opencode models`
    for the Zen catalogue (several hundred, embedding models included) and
    Ollama's `/api/tags` with capability detection. Never raises on an
    unreachable provider — it reports the reason instead, because a settings
    page that 500s when Ollama is down cannot explain that Ollama is down.
    """
    return await discovery.discover(provider)


class EmbeddingSet(BaseModel):
    backend: str = Field(..., max_length=64)
    model: str = Field(..., min_length=1, max_length=255)


@router.get("/embedding")
async def embedding_current():
    """The selected embedding backend and model."""
    return embeddings.current()


@router.post("/embedding")
async def embedding_select(body: EmbeddingSet):
    """Choose the embedding backend and model.

    Applies immediately — the cached local model is dropped so the next call
    loads the new one. Returns the width-compatibility verdict, because the
    vector stores are fixed at 384 dims and a mismatch makes retrieval return
    noise rather than raising.
    """
    try:
        return embeddings.select(body.backend, body.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/embedding/test")
async def embedding_test(body: EmbeddingSet):
    """Embed a probe string with the chosen model and report the width.

    Proving the model answers matters for the same reason as `/api/llm/test`: a
    configured embedder that cannot be loaded looks identical to a working one
    until retrieval quietly returns nothing.
    """
    try:
        embeddings.select(body.backend, body.model)
        vectors = await embeddings.embed(["council of minds embedding probe"])
    except Exception as e:
        return {
            "ok": False,
            "backend": body.backend,
            "model": body.model,
            "error": str(e)[:300],
            "diagnosis": diagnose_call_error(str(e)),
        }

    width = embeddings.width_of(vectors)
    matches_store = width == embeddings.VECTOR_DIM
    return {
        "ok": True,
        "backend": embeddings.configured_backend(),
        "model": embeddings.configured_model(),
        "dimension": width,
        "expected_dimension": embeddings.VECTOR_DIM,
        "matches_store": matches_store,
        "note": (
            None if matches_store else
            f"The vector stores are {embeddings.VECTOR_DIM}-dimensional. "
            f"{body.model} returns {width}, so cosine search over existing "
            "chunks will return noise. Re-embed, or choose a "
            f"{embeddings.VECTOR_DIM}-dim model."
        ),
    }


class TestRequest(BaseModel):
    model_id: str
    provider: Optional[str] = None


@router.post("/test")
async def test(body: TestRequest):
    """Send a real completion to confirm a model answers.

    Exists because "the provider has a key" and "this model actually works"
    are different claims, and only the second one matters. A key can be
    perfectly valid and still unable to serve a single completion — Zen's free
    tier is locked to the OpenCode client, and an unfunded account rejects
    paid models. Both are reported here with a `diagnosis`.
    """
    return await test_model(body.model_id, body.provider)


@router.post("/verify")
async def verify(body: TestRequest = TestRequest(model_id=""), provider: Optional[str] = None):
    """Prove the default model is callable, and explain it if it is not.

    `GET /api/health` only reports that a key is *configured*. This actually
    calls the model, which is what the setup wizard needs before telling the
    user they are ready.
    """
    model_id = body.model_id
    if not model_id:
        try:
            model_id = roles.model_for("MODERATOR")
        except Exception:
            model_id = ""
    result = await test_model(model_id or "big-pickle", body.provider or provider)
    result["note"] = (
        "A key being present does not mean it can serve requests."
        if not result.get("ok")
        else "The provider answered a real completion."
    )
    return result


@router.post("/reload")
async def reload_overrides():
    """Re-read persisted role overrides (after editing .env, for instance)."""
    loaded = await roles.load_overrides()
    return {"loaded": loaded, "count": len(loaded)}