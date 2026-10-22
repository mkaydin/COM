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


def build_kwargs(
    role: str = DEFAULT_ROLE,
    provider: Optional[providers.Provider] = None,
    **overrides: Any,
) -> dict:
    """Assemble completion kwargs for a role.

    `provider` is a named argument, not an override: it selects the transport,
    and letting it fall into `**overrides` would send a stray `provider=` to
    the model. Pass it explicitly in tests and tooling to resolve against a
    provider other than the active one.

    `api_base` and `api_key` are always explicit on LiteLLM transports.
    `response_format` is only sent when the resolved model is known to honour
    JSON mode; agents rely on `agents.json_utils.extract_json` for anything else.
    """
    from config import get_settings

    s = get_settings()
    # Resolve the role's own provider, not the global one. This is what lets the
    # Oracle run on a local Ollama model while the Moderator uses a free Zen one.
    provider = provider or roles.provider_for(role)

    # The CLI transport has its own credential story and does not take
    # api_base/api_key, so only LiteLLM-backed providers need the guard.
    if not provider.transport:
        providers.require_credential(provider)

    bare = overrides.pop("model", None) or roles.model_for(role, provider)
    routed = provider.model_id(bare)

    kwargs: dict[str, Any] = {"model": routed}
    if not provider.transport:
        kwargs.update({
            "api_base": provider.base_url,
            "api_key": provider.api_key or providers.PLACEHOLDER_KEY,
        })
        # Some endpoints need headers to route at all — the Go subscription
        # answers 400 without `x-opencode-session`. Sent only when the provider
        # declares them, so Zen and Ollama requests stay byte-identical.
        if provider.extra_headers:
            kwargs["extra_headers"] = dict(provider.extra_headers)
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
    from agents.retry import llm_call_with_retry

    # The role decides its own provider. `build_kwargs` resolves the model
    # against the same provider, so the model id and the transport always agree —
    # previously both used the single global provider, which made per-character
    # provider selection impossible.
    provider = roles.provider_for(role)
    kwargs = build_kwargs(
        role=role, provider=provider, messages=messages,
        temperature=temperature, max_tokens=max_tokens, **extra,
    )
    logger.debug(
        "llm_call", role=role, provider=provider.id, model=kwargs["model"],
        temp=temperature, max_tokens=max_tokens,
    )

    if provider.transport:
        import importlib

        module = importlib.import_module(f"llm.{provider.transport}")
        completion_fn = _cli_completion(module)
    else:
        from litellm import completion as completion_fn

    return await llm_call_with_retry(completion_fn, **kwargs)


def _cli_completion(module):
    """Adapt a CLI transport to LiteLLM's `completion(messages=..., **kw)`.

    The CLI transport has no notion of temperature or max_tokens — `opencode
    run` exposes neither — so those are accepted and ignored rather than
    passed through as unknown flags.
    """

    async def _call(**kwargs):
        return await module.complete(**kwargs)

    return _call


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

    if provider.id in ("opencode", "opencode-cli", "opencode-go"):
        # None of these has a usable `/models` we can query for a free list:
        # the CLI resolves through the binary, Zen's free entries are refused
        # to standalone clients, and the Go endpoint 404s on `/models`.
        out["reachable"] = bool(provider.api_key) or provider.id == "opencode-cli"
        if provider.id == "opencode-go":
            # A subscription has no free tier, so the useful list is the set of
            # models this build will actually route to.
            out["catalogue"] = [
                {
                    "id": model,
                    "name": model,
                    "roles": sorted(
                        role for role, default in provider.role_defaults.items()
                        if default == model
                    ),
                }
                for model in sorted(set(provider.role_defaults.values()))
            ]
            out["paid_suggestions"] = []
        out["hint"] = (
            None
            if provider.id == "opencode-cli" or provider.api_key
            else "Set OPENCODE_API_KEY to list the live catalogue."
        )
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

    # Each role resolves its own provider, so pass none here — pinning the global
    # one would report every role as being on it and hide a broken override.
    roles_map = []
    try:
        roles_map = roles.all_roles()
    except Exception as e:
        return {"ok": False, "provider": provider.id, "error": str(e)}

    # A role is only healthy if *its own* provider is usable. One character
    # pinned to a dead Ollama should not be masked by five working ones.
    unhealthy = [r["role"] for r in roles_map if r.get("provider_error")]

    ok = bool(provider.api_key) or not provider.requires_key
    detail = {
        "ok": ok and not unhealthy,
        "provider": provider.id,
        "transport": provider.transport or "litellm",
        "roles": roles_map,
        "unhealthy_roles": unhealthy,
    }

    if provider.transport == "cli_provider":
        # Reachability is whether the binary exists, not whether an endpoint
        # answers — the two fail for completely different reasons and the hint
        # has to say which.
        from llm import cli_provider

        cli = await cli_provider.health()
        detail.update({
            "reachable": cli.get("reachable"),
            "binary": cli.get("binary"),
            "hint": None if cli.get("ok") else cli.get("hint"),
        })
        ok = ok and bool(cli.get("ok"))

    elif provider.needs_local_model:
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


async def test_model(model_id: str = "", provider_id: Optional[str] = None) -> dict:
    """Send a trivial completion to verify a model actually answers.

    An empty `model_id` verifies the role default — and the response reports
    which model that resolved to, which is the point of the call.
    """
    provider = providers.get(provider_id)
    if not model_id:
        try:
            model_id = roles.model_for(DEFAULT_ROLE, provider)
        except Exception:
            model_id = ""
    if provider.requires_key and not provider.api_key:
        return {"ok": False, "model": model_id, "provider": provider.id,
                "error": "provider has no API key"}
    try:
        # `build_kwargs` prefers the explicit `model=`, so the probe really
        # tests the model the user picked — but the transport must be the
        # probed provider, not the one the MODERATOR role happens to use.
        kwargs = build_kwargs(
            role=DEFAULT_ROLE, provider=provider, model=model_id,
            temperature=0.0, max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
        if provider.transport:
            import importlib

            completion_fn = _cli_completion(importlib.import_module(f"llm.{provider.transport}"))
        else:
            from litellm import completion as completion_fn

        from agents.retry import llm_call_with_retry

        resp = await llm_call_with_retry(completion_fn, **kwargs)
        # Reach through the wrapper's own accessor rather than
        # `resp.choices[0].message.content`: the CLI transport's first choice
        # is a dict, not an object.
        text = (resp.content or "").strip()
        return {"ok": bool(text), "model": model_id, "provider": provider.id,
                "sample": text[:120], "callable": True}
    except Exception as e:
        message = str(e)
        return {
            "ok": False,
            "callable": False,
            "model": model_id,
            "provider": provider.id,
            "error": message[:300],
            "diagnosis": diagnose_call_error(message),
        }