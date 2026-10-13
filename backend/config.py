from pydantic_settings import BaseSettings
from typing import Optional
import os
import logging

logger = logging.getLogger(__name__)

# Council of Minds runs exclusively against a local Ollama server via its
# OpenAI-compatible endpoint. There is no provider registry and no runtime
# provider switching — the model is chosen with LLM_MODEL / OLLAMA_MODEL.
#
# Note: LiteLLM's `openai/` prefix routes through the OpenAI client, which
# requires a non-empty key. Ollama ignores the value.
OLLAMA_API_KEY_PLACEHOLDER = "ollama"


class Settings(BaseSettings):
    backend_port: int = 8734
    log_level: str = "INFO"
    log_dir: str = "/tmp/com-logs"

    # --- Ollama -----------------------------------------------------------
    ollama_host: str = "localhost"
    ollama_port: int = 11434
    llm_model: str = "openai/gemma4:e4b"
    supports_json_mode: bool = False

    # --- Storage ----------------------------------------------------------
    postgres_url: str = "postgresql+asyncpg://council:council_dev@localhost:5432/council"
    neo4j_url: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "council_dev"
    chroma_host: str = "localhost"
    chroma_port: int = 8000

    # --- Web intelligence (Divination) --------------------------------------
    searxng_url: str = "http://localhost:8888"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def ollama_base_url(self) -> str:
        """OpenAI-compatible base URL served by Ollama."""
        return f"http://{self.ollama_host}:{self.ollama_port}"

    @property
    def llm_api_base(self) -> str:
        return f"{self.ollama_base_url}/v1"

    @property
    def llm_api_key(self) -> str:
        return OLLAMA_API_KEY_PLACEHOLDER

    @property
    def ollama_tags_url(self) -> str:
        """Native Ollama endpoint listing installed models."""
        return f"{self.ollama_base_url}/api/tags"

    @property
    def bare_llm_model(self) -> str:
        """`llm_model` without the LiteLLM routing prefix (e.g. `gemma4:e4b`)."""
        return self.llm_model.split("/", 1)[-1]


_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
        _sync_env(_settings_instance)
    return _settings_instance


def _sync_env(s: Settings):
    """Mirror the Ollama endpoint into the env vars LiteLLM may read.

    Kept for anything that constructs its own client, but completion calls
    pass api_base/api_key explicitly — see `get_completion_kwargs`. Relying on
    ambient OPENAI_BASE_URL alone is how the Council ended up talking to
    whatever proxy happened to be exported in the shell.
    """
    os.environ["OPENAI_API_BASE"] = s.llm_api_base
    os.environ["OPENAI_API_KEY"] = s.llm_api_key


def get_completion_kwargs(**overrides) -> dict:
    """Build LLM completion kwargs.

    api_base and api_key are always passed explicitly so a stray OPENAI_*
    variable in the environment cannot redirect the Council away from Ollama.

    `response_format` is only sent when the served model is known to honour
    JSON mode. Small local models usually do not, and agents rely on
    `agents.json_utils.extract_json` to parse their output.
    """
    s = get_settings()
    kwargs = {
        "model": s.llm_model,
        "api_base": s.llm_api_base,
        "api_key": s.llm_api_key,
    }
    if s.supports_json_mode and "response_format" not in overrides:
        kwargs["response_format"] = {"type": "json_object"}
    kwargs.update(overrides)
    return kwargs


async def llm_complete(messages: list, temperature: float = 0.3, max_tokens: int = 2048, **extra):
    """Single entry point for every LLM call in the Council.

    Wraps LiteLLM with the retry/backoff helper. Agents must not call
    `litellm.completion` directly.
    """
    from litellm import completion
    from agents.retry import llm_call_with_retry

    return await llm_call_with_retry(
        completion,
        **get_completion_kwargs(messages=messages, temperature=temperature, max_tokens=max_tokens, **extra)
    )
