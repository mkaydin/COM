"""Provider registry.

Every LLM call in the Council goes through a provider. A provider answers
three questions: which base URL, which API key, and which LiteLLM model
prefix to use.

Four providers, in the order they should be tried:

* ``opencode-go`` — the default. The Go subscription over plain HTTP, paid-tier
  models on a subscription, no CLI involved. Sends ``x-opencode-session``.
* ``opencode`` — Zen over plain HTTP. Free models are refused for standalone
  clients, so this needs account funds.
* ``opencode-cli`` — shells out to the ``opencode`` CLI. Still the only route
  to Zen's **free** models, because the API answers ``FreeTierError`` for
  anything outside the client.
* ``ollama`` — secondary and local. Free and private, but a model tag has to be
  pulled first.

``opencode`` and ``opencode-cli`` share the same free-model defaults, so
switching between them keeps the six-character free cast intact. ``opencode-go``
has its own table, because spreading across models exists to dodge free-tier
rate limits that a subscription does not have.

Adding a provider means adding one `Provider` and one branch in `BUILDERS`;
nothing else in the codebase needs to change.
"""

import uuid
from dataclasses import dataclass, field
from typing import Optional

from llm import catalogue
from logging_config import get_logger

#: Generated once per process; see `_go_session`.
_OPENCODE_SESSION: Optional[str] = None

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
    #: Headers every request to this provider must carry. The Go endpoint
    #: refuses to route without `x-opencode-session`.
    extra_headers: dict = field(default_factory=dict)

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


def _go_session() -> str:
    """The `x-opencode-session` value, stable for the life of the process.

    A client-session identifier rather than a credential: the endpoint uses it
    to route the request and refuses outright without one. Honour
    `OPENCODE_SESSION` so a restart can stay in the same session.
    """
    global _OPENCODE_SESSION
    from config import get_settings

    configured = (get_settings().opencode_session or "").strip()
    if configured:
        return configured
    if _OPENCODE_SESSION is None:
        _OPENCODE_SESSION = str(uuid.uuid4())
    return _OPENCODE_SESSION


def _opencode_go() -> Provider:
    from config import get_settings

    s = get_settings()
    return Provider(
        id="opencode-go",
        label="OpenCode Go (subscription)",
        base_url=(s.opencode_go_base_url or catalogue.OPENCODE_GO_DEFAULT_BASE_URL).rstrip("/"),
        # Same credential as Zen; the two endpoints differ by path, not key.
        api_key=s.opencode_api_key or "",
        requires_key=True,
        role_defaults=catalogue.OPENCODE_GO_ROLE_DEFAULTS,
        extra_headers={"x-opencode-session": _go_session()},
        notes=(
            "Default. Paid-tier models on the Go subscription over HTTP, no CLI "
            "involved. Requires OPENCODE_API_KEY and sends x-opencode-session."
        ),
    )


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
        notes=(
            "Zen over HTTP. Free-tier models are refused for standalone clients "
            "— use opencode-cli for those — and paid models need account funds."
        ),
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
    # Ordered by preference: the subscription first, the local fallback last.
    "opencode-go": _opencode_go,
    "opencode": _opencode,
    "opencode-cli": _opencode_cli,
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

    pid = (provider_id or get_settings().llm_provider or "opencode-go").strip().lower()
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
        "active": provider.id == (s.llm_provider or "opencode-go").lower(),
        "free_models": catalogue.free_models_for(provider.id),
        "role_defaults": provider.role_defaults,
    }