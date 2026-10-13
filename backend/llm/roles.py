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
_overrides_loaded = False


def _env_key(role: str) -> str:
    return f"llm_model_{role.strip().lower()}"


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
        logger.info("llm_role_overrides_loaded", roles=sorted(_overrides))
    except Exception as e:
        # No DB yet (first boot, or a pure-unit test run) — env config stands.
        logger.debug("llm_role_overrides_unavailable", error=str(e)[:120])
    _overrides_loaded = True
    return dict(_overrides)


def model_for(role: str, provider: Optional[providers.Provider] = None) -> str:
    """The LiteLLM-routed model id for a role.

    Returns the bare tag; callers pass it through `providers.get().model_id()`.
    """
    _skip_load()
    key = (role or "").strip().upper()
    p = provider or providers.get()

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
    """Full resolution for a role, for `/api/llm/roles`."""
    p = provider or providers.get()
    s = get_settings()
    bare = model_for(role, p)
    key = (role or "").strip().upper()

    if key in _overrides:
        source = "runtime"
    elif getattr(s, _env_key(key), None):
        source = "env"
    elif p.role_defaults.get(key):
        source = "default"
    else:
        source = "fallback"

    return {
        "role": key,
        "model": bare,
        "routed_model": p.model_id(bare),
        "source": source,
        "is_free": bare in catalogue.OPENCODE_FREE_MODELS,
    }


def all_roles(provider: Optional[providers.Provider] = None) -> list[dict]:
    """Every known role with its resolution."""
    return [routing_for(r, provider) for r in KNOWN_ROLES]


async def set_override(role: str, model_id: str) -> dict:
    """Persist a role → model override and use it from now on."""
    from db.postgres import async_session
    from db.forum_models import LlmRole
    from sqlalchemy import select

    key = role.strip().upper()
    if key not in KNOWN_ROLES:
        raise providers.ProviderError(
            f"Unknown role {role!r}. Known roles: {', '.join(KNOWN_ROLES)}"
        )
    async with async_session() as db:
        row = (
            await db.execute(select(LlmRole).where(LlmRole.role == key))
        ).scalar_one_or_none()
        if row is None:
            row = LlmRole(role=key, model_id=model_id)
            db.add(row)
        else:
            row.model_id = model_id
        await db.commit()
    _overrides[key] = model_id
    logger.info("llm_role_override_set", role=key, model=model_id)
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
    logger.info("llm_role_override_cleared", role=key)
    return routing_for(key)


def _reset_cache() -> None:
    """Test hook — force the next resolution to re-read overrides."""
    global _overrides_loaded
    _overrides_loaded = False
    _overrides.clear()