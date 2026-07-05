from pydantic_settings import BaseSettings
from typing import Optional
import os
import json
import logging

logger = logging.getLogger(__name__)

PROVIDERS = {
    # "glm": {  # Deactivated — full local AI mode
    #     "label": "Z.ai GLM-5.2",
    #     "llm_model": "openai/glm-5.2",
    #     "llm_api_key": "0d716e1f4b3e414bba31387be0308cc0.W4tqTUQ38TnzTX1M",
    #     "llm_api_base": "https://api.z.ai/api/coding/paas/v4",
    #     "supports_json_mode": True,
    #     "max_tokens_default": 2048,
    # },
}

DIVINATION_PROVIDERS = {
    "ollama": {
        "label": "Ollama (ornith:9b)",
        "llm_model": "openai/ornith:9b",
        "llm_api_key": "ollama",
        "llm_api_base": "http://localhost:11434/v1",
        "supports_json_mode": False,
        "max_tokens_default": 8192,
    },
}


class Settings(BaseSettings):
    backend_port: int = 8734
    log_level: str = "INFO"
    log_dir: str = "/tmp/com-logs"

    llm_provider: str = "openai"
    llm_model: str = "openai/ornith:9b"
    llm_api_key: str = "ollama"
    llm_api_base: str = "http://localhost:11434/v1"
    active_provider: str = "ollama"
    supports_json_mode: bool = False

    postgres_url: str = "postgresql+asyncpg://council:council_dev@localhost:5432/council"
    neo4j_url: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "council_dev"
    redis_url: str = "redis://localhost:6379/0"
    chroma_host: str = "localhost"
    chroma_port: int = 8000

    ollama_host: str = "localhost"
    ollama_port: int = 11434
    embedding_model: str = "qwen3-embedding:0.6b"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
        _sync_env(_settings_instance)
    return _settings_instance


def _sync_env(s: Settings):
    if s.llm_api_base:
        os.environ["OPENAI_API_BASE"] = s.llm_api_base
    if s.llm_api_key:
        os.environ["OPENAI_API_KEY"] = s.llm_api_key


def switch_provider(provider_key: str):
    """Switch active LLM provider at runtime.

    Mutates the cached Settings object so all agents see the new values
    on their next attribute access.
    """
    global _settings_instance
    if provider_key not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider_key}")

    p = PROVIDERS[provider_key]
    s = get_settings()

    s.active_provider = provider_key
    s.llm_model = p["llm_model"]
    s.llm_api_key = p["llm_api_key"]
    s.llm_api_base = p["llm_api_base"]
    s.supports_json_mode = p["supports_json_mode"]

    _sync_env(s)
    _persist_provider_to_env(provider_key)

    logger.info(f"provider_switched provider={provider_key} model={p['llm_model']}")
    return p


def _persist_provider_to_env(provider_key: str):
    """Write the selected provider into .env so it survives restarts."""
    from pathlib import Path
    env_path = Path(__file__).parent / ".env"
    p = PROVIDERS[provider_key]

    lines = []
    if env_path.exists():
        existing = {}
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v
        existing["LLM_MODEL"] = p["llm_model"]
        existing["LLM_API_KEY"] = p["llm_api_key"]
        existing["LLM_API_BASE"] = p["llm_api_base"]
        existing["ACTIVE_PROVIDER"] = provider_key
        for k, v in existing.items():
            lines.append(f"{k}={v}")
    else:
        lines.append(f"LLM_MODEL={p['llm_model']}")
        lines.append(f"LLM_API_KEY={p['llm_api_key']}")
        lines.append(f"LLM_API_BASE={p['llm_api_base']}")
        lines.append(f"ACTIVE_PROVIDER={provider_key}")

    env_path.write_text("\n".join(lines) + "\n")


def get_completion_kwargs(**overrides) -> dict:
    """Build LLM completion kwargs with provider-specific settings.

    Conditionally includes response_format only when the active provider
    supports JSON mode.
    """
    s = get_settings()
    kwargs = {"model": s.llm_model}
    if s.supports_json_mode and "response_format" not in overrides:
        kwargs["response_format"] = {"type": "json_object"}
    kwargs.update(overrides)
    return kwargs


async def llm_complete(messages: list, temperature: float = 0.3, max_tokens: int = 2048, provider: str = None, **extra):
    """High-level LLM completion with retry + provider-specific config.

    If provider is specified, temporarily switches to that provider for this call only.
    All agents should use this instead of calling litellm.completion directly.
    """
    from litellm import completion
    from agents.retry import llm_call_with_retry
    import os

    s = get_settings()

    # Temporarily switch provider if requested
    if provider and provider != s.active_provider and provider in DIVINATION_PROVIDERS:
        p = DIVINATION_PROVIDERS[provider]
        orig = (s.llm_model, s.llm_api_key, s.llm_api_base, s.supports_json_mode)
        s.llm_model = p["llm_model"]
        s.llm_api_key = p["llm_api_key"]
        s.llm_api_base = p["llm_api_base"]
        s.supports_json_mode = p["supports_json_mode"]
        os.environ["OPENAI_API_KEY"] = p["llm_api_key"]
        os.environ["OPENAI_API_BASE"] = p["llm_api_base"]
    else:
        orig = None

    try:
        return await llm_call_with_retry(
            completion,
            **get_completion_kwargs(messages=messages, temperature=temperature, max_tokens=max_tokens, **extra)
        )
    finally:
        if orig:
            s.llm_model, s.llm_api_key, s.llm_api_base, s.supports_json_mode = orig
            os.environ["OPENAI_API_KEY"] = orig[1]
            os.environ["OPENAI_API_BASE"] = orig[2]
