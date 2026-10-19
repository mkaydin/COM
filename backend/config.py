from pydantic_settings import BaseSettings
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Settings only. Provider resolution lives in `llm/providers.py` and the
# role -> model binding in `llm/roles.py`; nothing here should reach for a
# specific vendor.


class Settings(BaseSettings):
    backend_port: int = 8734
    log_level: str = "INFO"
    log_dir: str = "/tmp/com-logs"

    # --- Provider selection -------------------------------------------------------
    # "opencode-cli"  default — free Zen models via the `opencode` CLI, because
    #                 the API refuses free models for standalone clients.
    # "opencode"     Zen over plain HTTP. Needs a funded account.
    # "ollama"       optional local.
    # See llm/providers.py.
    llm_provider: str = "opencode-cli"
    llm_model_default: str = "big-pickle"

    # Deprecated single-model setting, kept so an existing `.env` written for
    # the Ollama-only setup still works. When set and `llm_model_default` was
    # not, it becomes the default for every role. See `default_model`.
    llm_model: Optional[str] = None

    # --- OpenCode ------------------------------------------------------------------
    #: Optional. The CLI uses its own credential store, so a key is not needed for
    #: the free tier; setting one lets paid models work over the same path.
    opencode_api_key: str = ""
    opencode_base_url: str = "https://opencode.ai/zen/v1"
    #: Path or name of the OpenCode binary for the `opencode-cli` provider.
    opencode_cli_bin: str = "opencode"

    # --- Ollama (optional local) ---------------------------------------------------
    ollama_host: str = "localhost"
    ollama_port: int = 11434

    # Per-role overrides. Any role omitted falls back to `llm_model_default`.
    # Character keys are lowercase; the field name is `llm_model_<role>`.
    llm_model_moderator: Optional[str] = None
    llm_model_oracle: Optional[str] = None
    llm_model_scholar: Optional[str] = None
    llm_model_sage: Optional[str] = None
    llm_model_analyst: Optional[str] = None
    llm_model_historian: Optional[str] = None

    # Per-role *provider* overrides — `llm_provider_<role>`. Set one to run a
    # single character through a different backend than the rest of the cast.
    # The model for that role is then resolved against this provider, not the
    # global one. None means "whatever `llm_provider` says".
    #
    # Declared explicitly rather than read ad-hoc: `getattr(settings, name, None)`
    # would work, but an undeclared key that pydantic never heard of is exactly
    # the kind of typo that silently does nothing.
    llm_provider_moderator: Optional[str] = None
    llm_provider_oracle: Optional[str] = None
    llm_provider_scholar: Optional[str] = None
    llm_provider_sage: Optional[str] = None
    llm_provider_analyst: Optional[str] = None
    llm_provider_historian: Optional[str] = None

    # --- Embeddings ----------------------------------------------------------------
    # One embedding model is shared by retrieval, the OKF bundle and ingestion.
    #
    # Default is `ollama` + `qwen3-embedding:0.6b`: 32k window (nothing is
    # truncated) and a Matryoshka head that emits exactly the 384 dimensions the
    # stores use, so it fits the existing schema with no migration.
    #
    # `local` stays available as the no-extra-services fallback, but
    # all-MiniLM-L6-v2 has a 256-token window against 300-500 token chunks, so
    # it silently drops most of a wiki page. `llm.embeddings` warns on load.
    #: One of `local`, `ollama`, `opencode-cli`, `opencode`.
    embedding_backend: str = "ollama"
    #: A sentence-transformers id for `local`; a provider's model id otherwise.
    embedding_model: str = "qwen3-embedding:0.6b"

    supports_json_mode: bool = False

    # --- Forum ---------------------------------------------------------------------
    forum_enabled: bool = True
    forum_max_rounds: int = 2
    #: Optional shared secret for POST /api/forum/intake. Empty = local only.
    forum_intake_key: str = ""

    # --- Storage --------------------------------------------------------------------
    postgres_url: str = "postgresql+asyncpg://council:council_dev@localhost:5432/council"
    neo4j_url: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "council_dev"
    chroma_host: str = "localhost"
    chroma_port: int = 8000

    # --- Web intelligence (Divination) ----------------------------------------------
    searxng_url: str = "http://localhost:8888"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def ollama_base_url(self) -> str:
        return f"http://{self.ollama_host}:{self.ollama_port}"

    @property
    def llm_api_base(self) -> str:
        """Ollama's OpenAI-compatible base URL."""
        return f"{self.ollama_base_url}/v1"

    @property
    def llm_api_key(self) -> str:
        from llm import providers

        return providers.PLACEHOLDER_KEY

    @property
    def ollama_tags_url(self) -> str:
        """Native Ollama endpoint listing installed models."""
        return f"{self.ollama_base_url}/api/tags"

    @property
    def bare_llm_model(self) -> str:
        """`llm_model` without the LiteLLM routing prefix."""
        return self.default_model.split("/", 1)[-1]

    @property
    def default_model(self) -> str:
        """The model used when no per-role override applies.

        `llm_model_default` wins when the operator set it explicitly; the
        deprecated `llm_model` is honoured only when it did not. Using
        `model_fields_set` rather than comparing against the default's literal
        value matters: an operator who deliberately set
        `LLM_MODEL_DEFAULT=big-pickle` *and* still has an old `LLM_MODEL` in
        their `.env` means the new one.
        """
        if "llm_model_default" in self.model_fields_set:
            raw = self.llm_model_default
        elif self.llm_model:
            raw = self.llm_model
        else:
            raw = self.llm_model_default
        return raw.split("/", 1)[-1]


_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


# Kept as a thin shim so existing call sites (`config.llm_complete`) keep
# working during the migration to `llm.llm_complete(role=...)`. New code
# should import from `llm` and pass a role.
async def llm_complete(messages: list, temperature: float = 0.3, max_tokens: int = 2048, **extra):
    from llm import llm_complete as _impl

    return await _impl(
        messages, role=extra.pop("role", "MODERATOR"),
        temperature=temperature, max_tokens=max_tokens, **extra
    )


def get_completion_kwargs(**overrides) -> dict:
    """Deprecated: use `llm.build_kwargs(role=...)`."""
    from llm import build_kwargs

    return build_kwargs(**overrides)