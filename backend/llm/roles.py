"""Role → model binding.

A "role" is what a character is (moderator, oracle, scholar, …), not which
provider is active. Resolution order for `model_for("ORACLE")`:

1. A runtime override stored in the `llm_roles` table (set via
   `POST /api/llm/roles`, survives restarts).
2. `LLM_MODEL_ORACLE` in `.env`.
3. The provider's built-in free default for that role.

Anything that does not resolve is a hard error at startup rather than a
surprising model in production.
"""

from typing import Optional

from config import get_settings
from llm import catalogue, providers
from logging_config import get_logger

logger = get_logger(__name__)

#: Every role the Council can dispatch to. Characters reference these by name,
#: so adding a character without adding its role here is caught by tests.
KNOWN_ROLES: tuple[str, ...] = (
    "MODERATOR",
    "ORACLE",
    "SCHOLAR",
    "SAGE",
    "ANALYST",
    "HISTORIAN",
)

#: Runtime overrides, loaded from the DB on first use. Kept module-level so a
#: role change does not require re-reading the table on every call.
_overrides: dict[str, str] = {}
#: Role → provider id. Separate from `_overrides` because a role may name a
#: model without naming a provider (NULL in the DB means "use the global
#: default"), and the two are set and cleared independently.
_provider_overrides: dict[str, str] = {}
_overrides_loaded = False


def _env_key(role: str) -> str:
    return f"llm_model_{role.strip().lower()}"


def provider_for(role: str, default: Optional[providers.Provider] = None):
    """Which provider this role speaks through.

    Per-character provider selection is what lets the Oracle run on a local
    Ollama model while the Moderator uses a free Zen model — previously one
    global `LLM_PROVIDER` decided for the whole cast.

    Resolution: the role's runtime override → `LLM_PROVIDER_<ROLE>` in `.env`
    → the global default. An override recorded before `llm_roles.provider`
    existed has NULL there, which correctly falls through to the default.

    Raises `ProviderError` for an unknown provider id: a typo in the settings
    UI must fail loudly rather than silently reverting to the global default
    and quietly changing which models run.
    """
    key = (role or "").strip().upper()

    override = _provider_overrides.get(key)
    if override:
        return providers.get(override)

    s = get_settings()
    env_value = getattr(s, f"llm_provider_{key.lower()}", None)
    if env_value:
        return providers.get(str(env_value))

    return default or providers.get()


def _skip_load() -> None:
    """Mark overrides as loaded without touching the DB.

    Used before the app's lifespan runs (unit tests, CLI tooling) so the
    resolution path never blocks on I/O it does not need.
    """
    global _overrides_loaded
    _overrides_loaded = True


async def load_overrides() -> dict[str, str]:
    """Read persisted role overrides. Called once from the app lifespan.

    Failure is non-fatal: env configuration still applies, which is why this
    logs at debug rather than raising.
    """
    global _overrides_loaded
    try:
        from db.postgres import async_session
        from db.forum_models import LlmRole
        from sqlalchemy import select

        async with async_session() as db:
            rows = (await db.execute(select(LlmRole))).scalars().all()
            _overrides.clear()
            _overrides.update({r.role.upper(): r.model_id for r in rows})
            # A NULL provider means "whichever provider is globally active",
            # which is what every row written before the column existed means.
            _provider_overrides.clear()
            _provider_overrides.update({
                r.role.upper(): r.provider
                for r in rows
                if getattr(r, "provider", None)
            })
        logger.info(
            "llm_role_overrides_loaded",
            roles=sorted(_overrides),
            per_role_providers=sorted(_provider_overrides),
        )
    except Exception as e:
        # No DB yet (first boot, or a pure-unit test run) — env config stands.
        logger.debug("llm_role_overrides_unavailable", error=str(e)[:120])
    _overrides_loaded = True
    return dict(_overrides)


def model_for(role: str, provider: Optional[providers.Provider] = None) -> str:
    """The model id for a role, bare.

    Returns the bare tag; callers pass it through `Provider.model_id()`.

    `provider` is the resolved provider when the caller already has one (that is
    what `provider_for` returns). When omitted, the role's own provider is
    resolved — so an Oracle pinned to Ollama gets Ollama's default model for
    the ORACLE role, not the globally-active provider's.
    """
    _skip_load()
    key = (role or "").strip().upper()
    p = provider or provider_for(key)

    if key in _overrides:
        return _overrides[key]

    s = get_settings()

    env_value = getattr(s, _env_key(key), None)
    if env_value:
        return str(env_value)

    # A role with a catalogue entry gets that provider's default.
    default = p.role_defaults.get(key)
    if default:
        return default

    # Otherwise fall back to the operator's single-model setting, then to the
    # provider's first default. A brand-new character must still get a usable
    # model rather than an empty string.
    if s.default_model:
        return s.default_model
    return next(iter(p.role_defaults.values()), "")


def routing_for(role: str, provider: Optional[providers.Provider] = None) -> dict:
    """Full resolution for a role, for `/api/llm/roles`.

    Reports the provider as well as the model, because a role pinned to Ollama
    resolves its model against *that* provider's defaults — so the two have to
    be reported together or the UI shows a model that looks wrong.
    """
    key = (role or "").strip().upper()
    provider_error = None
    try:
        p = provider or provider_for(key)
    except providers.ProviderError as e:
        # An unknown provider id in the settings UI must be *visible*, not
        # silently downgraded to the global default: the user believes they
        # pointed this character somewhere and they would be wrong.
        provider_error = str(e)
        p = provider or providers.get()
    s = get_settings()
    bare = model_for(role, p)

    if key in _overrides:
        source = "runtime"
    elif getattr(s, _env_key(key), None):
        source = "env"
    elif p.role_defaults.get(key):
        source = "default"
    else:
        source = "fallback"

    if key in _provider_overrides:
        provider_source = "runtime"
    elif getattr(s, f"llm_provider_{key.lower()}", None):
        provider_source = "env"
    else:
        provider_source = "global"

    return {
        "role": key,
        "model": bare,
        "routed_model": p.model_id(bare),
        "provider": p.id,
        "provider_label": p.label,
        "provider_source": provider_source,
        "transport": p.transport or "litellm",
        "source": source,
        "is_free": bare in catalogue.OPENCODE_FREE_MODELS,
        "provider_error": provider_error,
    }


def all_roles(provider: Optional[providers.Provider] = None) -> list[dict]:
    """Every known role with its resolution.

    `provider` pins the provider for every role; normally it is omitted so each
    role resolves its own.
    """
    return [routing_for(r, provider) for r in KNOWN_ROLES]


async def set_override(
    role: str,
    model_id: str,
    provider_id: Optional[str] = None,
) -> dict:
    """Persist a role → (provider, model) override and use it from now on.

    `provider_id=None` pins the model but leaves the role on the globally
    active provider — which is what the pre-provider UI did, and is still a
    useful thing to be able to say explicitly.
    """
    from db.postgres import async_session
    from db.forum_models import LlmRole
    from sqlalchemy import select

    key = role.strip().upper()
    if key not in KNOWN_ROLES:
        raise providers.ProviderError(
            f"Unknown role {role!r}. Known roles: {', '.join(KNOWN_ROLES)}"
        )
    # Validate before persisting: an unknown provider must not be written and
    # then blow up on every subsequent call to this role.
    if provider_id:
        providers.get(provider_id)

    async with async_session() as db:
        row = (
            await db.execute(select(LlmRole).where(LlmRole.role == key))
        ).scalar_one_or_none()
        if row is None:
            row = LlmRole(role=key, model_id=model_id, provider=provider_id or None)
            db.add(row)
        else:
            row.model_id = model_id
            row.provider = provider_id or None
        await db.commit()
    _overrides[key] = model_id
    if provider_id:
        _provider_overrides[key] = provider_id
    else:
        _provider_overrides.pop(key, None)
    logger.info("llm_role_override_set", role=key, model=model_id, provider=provider_id)
    return routing_for(key)


async def clear_override(role: str) -> dict:
    """Drop a runtime override, reverting to env/default."""
    from db.postgres import async_session
    from db.forum_models import LlmRole
    from sqlalchemy import select

    key = role.strip().upper()
    async with async_session() as db:
        row = (
            await db.execute(select(LlmRole).where(LlmRole.role == key))
        ).scalar_one_or_none()
        if row is not None:
            await db.delete(row)
            await db.commit()
    _overrides.pop(key, None)
    _provider_overrides.pop(key, None)
    logger.info("llm_role_override_cleared", role=key)
    return routing_for(key)


def _reset_cache() -> None:
    """Test hook — force the next resolution to re-read overrides."""
    global _overrides_loaded
    _overrides_loaded = False
    _overrides.clear()
    _provider_overrides.clear()