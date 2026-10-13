"""Helpers for talking to the local Ollama server.

Scoped to the *optional* local provider. Used by `/api/ollama/status` and the
setup wizard to verify that a configured local model is actually pulled.

A non-empty API key is not evidence that an LLM works — a missing model tag
makes every agent call fail — which is why this exists even though the
provider-agnostic health check in `llm.health()` is the primary path.
"""

import asyncio
from typing import Optional

import httpx

from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)

_TIMEOUT = 3.0


async def list_installed_models() -> tuple[bool, list[str]]:
    """Return (reachable, model_names) for the configured Ollama server."""
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(s.ollama_tags_url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        logger.warning("ollama_unreachable", url=s.ollama_tags_url, error=str(e))
        return False, []

    names = [m.get("name", "") for m in payload.get("models", []) if m.get("name")]
    return True, names


def _model_matches(candidate: str, installed: list[str]) -> bool:
    """Match a configured model against installed tags.

    Ollama resolves `name:latest` to `name:<quant>`, so an exact match is not
    always available. A configured `gemma4:e4b` should satisfy an installed
    `gemma4:e4b-q4_0`, but not an installed `gemma4:e4b-extra`.
    """
    if candidate in installed:
        return True
    for name in installed:
        if name.startswith(candidate + "-") or candidate.startswith(name + "-"):
            return True
    return False


async def ollama_status() -> dict:
    """Structured view of the local model server, for the UI and health check."""
    s = get_settings()
    reachable, installed = await list_installed_models()
    bare = s.bare_llm_model
    present = _model_matches(bare, installed) if reachable else False

    return {
        "reachable": reachable,
        "model": bare,
        "model_present": present,
        "supports_json_mode": s.supports_json_mode,
        "installed": sorted(installed),
    }


async def llm_health() -> dict:
    """Health detail for `/api/health`.

    Ollama-specific; the provider-agnostic version is `llm.health()`.
    """
    status = await ollama_status()
    ready = status["reachable"] and status["model_present"]
    if not status["reachable"]:
        hint = f"Ollama unreachable at {get_settings().ollama_base_url}"
    elif not status["model_present"]:
        hint = f"Model '{status['model']}' not installed. Run: ollama pull {status['model']}"
    else:
        hint = None
    return {"ok": ready, **status, "hint": hint}


async def pull_model(model: Optional[str] = None) -> tuple[bool, str]:
    """Ask Ollama to pull a model. Long-running; intended for explicit user action."""
    s = get_settings()
    name = model or s.bare_llm_model
    try:
        proc = await asyncio.create_subprocess_exec(
            "ollama", "pull", name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return False, "ollama CLI not found on PATH"
    await proc.communicate()
    return proc.returncode == 0, name
