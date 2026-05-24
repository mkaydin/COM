from pydantic_settings import BaseSettings
from functools import lru_cache
import os


class Settings(BaseSettings):
    backend_port: int = 8734
    log_level: str = "INFO"
    log_dir: str = "/tmp/com-logs"

    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_api_key: str = ""

    postgres_url: str = "postgresql+asyncpg://council:council_dev@localhost:5432/council"
    neo4j_url: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "council_dev"
    redis_url: str = "redis://localhost:6379/0"
    chroma_host: str = "localhost"
    chroma_port: int = 8000

    embedding_model: str = "text-embedding-3-small"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
