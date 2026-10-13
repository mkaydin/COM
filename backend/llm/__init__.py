"""The single entry point for every LLM call in the Council.

`llm_complete()` replaces the old `config.llm_complete()`. The difference that
matters: it takes a **role** rather than reaching for one global model, so the
Moderator, the Oracle and the Sage can each run on a different model — which
is the whole point of the free-model-per-character setup.

Both providers are reached through LiteLLM's ``openai/`` prefix with an
explicit ``api_base``/``api_key``. Never relying on ambient ``OPENAI_*``
environment variables: that is how this project once ended up talking to
whatever proxy happened to be exported in the shell.
"""

from typing import Any, Optional

from llm import catalogue, providers, roles
from logging_config import get_logger

logger = get_logger(__name__)

#: Fallback used when a caller does not name a role.
DEFAULT_ROLE = "MODERATOR"


def build_kwargs(role: str = DEFAULT_ROLE, **overrides: Any) -> dict:
    """Assemble LiteLLM kwargs for a role.

    `api_base` and `api_key` are always explicit. `response_format` is only
    sent when the resolved model is known to honour JSON mode; agents rely on
    `agents.json_utils.extract_json` to parse anything else.
    """
    from config import get_settings

    s = get_settings()
    provider = providers.get()
    providers.require_credential(provider)

    bare = overrides.pop("model", None) or roles.model_for(role, provider)
    routed = provider.model_id(bare)

    kwargs: dict[str, Any] = {
        "model": routed,
        "api_base": provider.base_url,
        "api_key": provider.api_key or providers.PLACEHOLDER_KEY,
    }
    if s.supports_json_mode and "response_format" not in overrides:
        kwargs["response_format"] = {"type": "json_object"}
    kwargs.update(overrides)
    return kwargs


async def llm_complete(
    messages: list,
    role: str = DEFAULT_ROLE,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    **extra: Any,
):
    """One LLM call, with retry/backoff, on the role's configured model.

    Agents must not call `litellm.completion` directly — going through here is
    what keeps provider selection, per-role models and retries in one place.
    """
    from litellm import completion
    from agents.retry import llm_call_with_retry

    kwargs = build_kwargs(
        role=role, messages=messages, temperature=temperature, max_tokens=max_tokens, **extra
    )
    logger.debug(
        "llm_call", role=role, model=kwargs["model"], temp=temperature, max_tokens=max_tokens
    )
    return await llm_call_with_retry(completion, **kwargs)


# --- health / introspection ---------------------------------------------------------


async def list_models(provider_id: Optional[str] = None) -> dict:
    """Models this provider can serve, merging catalogue + live listing."""
    from config import get_settings

    provider = providers.get(provider_id)
    s = get_settings()
    out: dict[str, Any] = {
        "provider": provider.id,
        "base_url": provider.base_url,
        "installed": [],
        "reachable": False,
        "catalogue": catalogue.free_models_for(provider.id),
        "unsupported_free": catalogue.unsupported_free_models(),
        "paid_suggestions": catalogue.OPENCODE_PAID_SUGGESTIONS
        if provider.id == "opencode"
        else [],
        "snapshot_date": catalogue.OPENCODE_SNAPSHOT_DATE,
        "hint": None,
    }

    if provider.id == "opencode":
        # No model list without a key; the catalogue still stands in.
        out["reachable"] = bool(provider.api_key)
        if not provider.api_key:
            out["hint"] = "Set OPENCODE_API_KEY to list the live catalogue."
        return out

    # ollama: the installed tags are the source of truth.
    from ollama_client import list_installed_models

    reachable, installed = await list_installed_models()
    out["reachable"] = reachable
    out["installed"] = sorted(installed)
    if not reachable:
        out["hint"] = f"Ollama unreachable at {provider.base_url}"
    elif not installed:
        out["hint"] = "No models pulled yet — run: ollama pull <model>"
    return out


async def health() -> dict:
    """Provider health for `/api/health`."""
    from config import get_settings

    s = get_settings()
    try:
        provider = providers.get()
    except providers.ProviderError as e:
        return {"ok": False, "provider": s.llm_provider, "error": str(e)}

    roles_map = []
    try:
        roles_map = roles.all_roles(provider)
    except Exception as e:
        return {"ok": False, "provider": provider.id, "error": str(e)}

    ok = bool(provider.api_key) or not provider.requires_key
    detail = {"ok": ok, "provider": provider.id, "roles": roles_map}

    if provider.needs_local_model:
        from ollama_client import ollama_status

        st = await ollama_status()
        detail.update(
            {
                "reachable": st["reachable"],
                "installed": st["installed"],
                "model_present": st["model_present"],
                "hint": None
                if st["reachable"] and st["model_present"]
                else (
                    f"Ollama unreachable at {provider.base_url}"
                    if not st["reachable"]
                    else f"Model not installed. Run: ollama pull {st['model']}"
                ),
            }
        )
        ok = ok and st["reachable"] and st["model_present"]
    elif not provider.api_key:
        detail["hint"] = "OPENCODE_API_KEY is not set — every agent call will fail."
    else:
        detail["hint"] = None

    detail["ok"] = ok
    # `ok` means "configured", not "proven to work". A key can be present and
    # still unable to serve a single completion — Zen's free tier is locked to
    # the OpenCode client, and an unfunded account rejects paid models with
    # "Insufficient account funds". Only POST /api/llm/test proves that.
    detail["configured"] = ok
    detail["callable"] = None
    detail["callable_note"] = (
        "not verified — POST /api/llm/test with a model id to prove it can "
        "actually serve a completion"
    )
    return detail


#: Substrings that identify a key which authenticates but cannot serve calls.
NOT_CALLABLE_HINTS = (
    "free tier can only be used from within opencode",
    "insufficient account funds",
    "model access is disabled",
    "model is unavailable",
)


def diagnose_call_error(message: str) -> Optional[str]:
    """Turn a provider error into an actionable explanation.

    The raw messages are not obvious: "OpenCode's free tier can only be used
    from within OpenCode" means the backend cannot use free models over the
    plain API at all, and "Insufficient account funds" means the account has no
    billing. Both surface identically to a user as "the key is set but nothing
    works", which is exactly the case worth explaining.
    """
    if not message:
        return None
    low = message.lower()
    if "free tier can only be used from within opencode" in low:
        return (
            "Zen's free models are locked to the OpenCode client and cannot be "
            "called from a standalone API client. Either route calls through "
            "the opencode CLI, or add billing to use paid models over HTTP."
        )
    if "insufficient account funds" in low:
        return (
            "The key is valid but the account has no billing, so paid models "
            "are refused. Add billing at opencode.ai, or use the opencode CLI "
            "for the free tier."
        )
    if "model access is disabled" in low:
        return "This key does not have access to that model."
    if "model is unavailable" in low:
        return "That model is currently unavailable at the provider."
    return None


async def test_model(model_id: str, provider_id: Optional[str] = None) -> dict:
    """Send a trivial completion to verify a model actually answers."""
    provider = providers.get(provider_id)
    if provider.requires_key and not provider.api_key:
        return {"ok": False, "model": model_id, "error": "provider has no API key"}
    try:
        resp = await llm_complete(
            [{"role": "user", "content": "Reply with the single word: ready"}],
            role=DEFAULT_ROLE,
            temperature=0.0,
            max_tokens=16,
            model=model_id,
        )
        text = (resp.choices[0].message.content or "").strip()
        return {"ok": bool(text), "model": model_id, "sample": text[:120],
                "callable": True}
    except Exception as e:
        message = str(e)
        return {
            "ok": False,
            "callable": False,
            "model": model_id,
            "error": message[:300],
            "diagnosis": diagnose_call_error(message),
        }