"""Provider registry.

Every LLM call in the Council goes through a provider. A provider answers
three questions: which base URL, which API key, and which LiteLLM model
prefix to use.

Three providers:

* ``opencode-cli`` — the default. Shells out to the OpenCode CLI. This is the
  only route to Zen's **free** models, because the API endpoint refuses them
  with ``FreeTierError`` for any standalone client.
* ``opencode`` — Zen over plain HTTP. Needs a funded account; fast.
* ``ollama`` — optional local. Fast, free, private, no key.

``opencode-cli`` and ``ollama`` share the same free-model defaults as
``opencode``, so switching to either keeps the six-character free cast intact.

Adding a provider means adding one `Provider` and one branch in `BUILDERS`;
nothing else in the codebase needs to change.
"""

from dataclasses import dataclass, field
from typing import Optional

from llm import catalogue
from logging_config import get_logger

logger = get_logger(__name__)

#: LiteLLM's OpenAI client refuses an empty key. Both providers ignore the
#: value; only its presence matters.
PLACEHOLDER_KEY = "com-local"


class ProviderError(RuntimeError):
    """The configured provider cannot serve the request."""


class MissingCredential(ProviderError):
    """The provider needs an API key that is not configured."""


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str
    api_key: str
    #: LiteLLM routing prefix. `openai/` for anything that speaks the
    #: OpenAI chat-completions shape, which is both of ours.
    litellm_prefix: str = "openai/"
    requires_key: bool = False
    #: Whether a model tag must exist locally before calls will succeed.
    needs_local_model: bool = False
    notes: str = ""
    #: Per-role defaults, kept per-provider so a switch never re-points a
    #: character at a remote model.
    role_defaults: dict = field(default_factory=dict)
    #: When set, calls go through this transport instead of LiteLLM. The
    #: value is the submodule under `llm/` implementing `complete()`.
    transport: Optional[str] = None

    def model_id(self, bare: str) -> str:
        """Prefix a bare model tag for routing.

        A non-LiteLLM transport (the CLI) takes the bare tag: it does its own
        prefixing, and `openai/big-pickle` is not a model it recognises.
        """
        if not bare:
            return ""
        if self.transport:
            return bare
        if "/" in bare and bare.split("/", 1)[0] in ("openai", "ollama", "hosted_vllm"):
            return bare
        return f"{self.litellm_prefix}{bare}"


def _opencode() -> Provider:
    from config import get_settings

    s = get_settings()
    return Provider(
        id="opencode",
        label="OpenCode Zen",
        base_url=(s.opencode_base_url or catalogue.OPENCODE_DEFAULT_BASE_URL).rstrip("/"),
        api_key=s.opencode_api_key or "",
        requires_key=True,
        role_defaults=catalogue.OPENCODE_ROLE_DEFAULTS,
        notes="Default. Free-tier models available; see /api/llm/models.",
    )


def _opencode_cli() -> Provider:
    from config import get_settings

    s = get_settings()
    return Provider(
        id="opencode-cli",
        label="OpenCode CLI (free models)",
        base_url="opencode://cli",
        api_key=s.opencode_api_key or "",
        requires_key=False,
        transport="cli_provider",
        role_defaults=catalogue.OPENCODE_ROLE_DEFAULTS,
        notes=(
            "Default. Free Zen models via the `opencode` CLI — the API refuses "
            "them for standalone clients. Slower per call; needs the CLI on PATH."
        ),
    )


def _ollama() -> Provider:
    from config import get_settings

    s = get_settings()
    return Provider(
        id="ollama",
        label="Ollama (local)",
        base_url=s.llm_api_base,
        api_key=PLACEHOLDER_KEY,
        needs_local_model=True,
        role_defaults=catalogue.OLLAMA_ROLE_DEFAULTS,
        notes="Optional local provider. Requires the model tag to be pulled.",
    )


_BUILDERS = {
    "opencode-cli": _opencode_cli,
    "opencode": _opencode,
    "ollama": _ollama,
}


def available() -> list[str]:
    """Known provider ids."""
    return list(_BUILDERS)


def get(provider_id: Optional[str] = None) -> Provider:
    """Resolve a provider, defaulting to the configured one.

    Raises `ProviderError` for an unknown id — a typo in `.env` must fail
    loudly rather than silently talking to the wrong endpoint.
    """
    from config import get_settings

    pid = (provider_id or get_settings().llm_provider or "opencode-cli").strip().lower()
    builder = _BUILDERS.get(pid)
    if builder is None:
        raise ProviderError(
            f"Unknown LLM provider {pid!r}. Known providers: {', '.join(available())}"
        )
    return builder()


def require_credential(provider: Provider) -> None:
    """Raise if a provider that needs a key has none."""
    if provider.requires_key and not provider.api_key:
        raise MissingCredential(
            f"Provider {provider.id!r} needs an API key. Set OPENCODE_API_KEY in backend/.env."
        )


def describe(provider: Provider) -> dict:
    """A provider's public shape, for `/api/llm/providers`."""
    from config import get_settings

    s = get_settings()
    return {
        "id": provider.id,
        "label": provider.label,
        "base_url": provider.base_url,
        "configured": bool(provider.api_key) or not provider.requires_key,
        "requires_key": provider.requires_key,
        "needs_local_model": provider.needs_local_model,
        "notes": provider.notes,
        "transport": provider.transport or "litellm",
        "active": provider.id == (s.llm_provider or "opencode-cli").lower(),
        "free_models": catalogue.free_models_for(provider.id),
        "role_defaults": provider.role_defaults,
    }