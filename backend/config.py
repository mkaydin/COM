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
    # "opencode" (OpenCode Zen) is the default; "ollama" is the optional local
    # provider. See llm/providers.py.
    llm_provider: str = "opencode"
    llm_model_default: str = "big-pickle"

    # Deprecated single-model setting, kept so an existing `.env` written for
    # the Ollama-only setup still works. When set and `llm_model_default` was
    # not, it becomes the default for every role. See `default_model`.
    llm_model: Optional[str] = None

    # --- OpenCode Zen --------------------------------------------------------------
    opencode_api_key: str = ""
    opencode_base_url: str = "https://opencode.ai/zen/v1"

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