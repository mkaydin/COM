"""Redis client for caching, Celery broker, and pub/sub."""
from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)
settings = get_settings()

_client = None


def get_client():
    global _client
    if _client is None:
        try:
            import redis
            _client = redis.from_url(settings.redis_url, decode_responses=True)
            _client.ping()
            logger.info("redis_connected")
        except Exception as e:
            logger.warning("redis_unavailable", error=str(e))
    return _client
