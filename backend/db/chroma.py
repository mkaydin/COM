"""ChromaDB client for vector document storage."""
from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)
settings = get_settings()

_client = None
_collection = None


def get_client():
    global _client
    if _client is None:
        try:
            import chromadb
            _client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
            logger.info("chromadb_connected")
        except Exception as e:
            logger.warning("chromadb_unavailable", error=str(e))
    return _client


def get_collection(name: str = "documents_shared"):
    global _collection
    client = get_client()
    if not client:
        return None
    try:
        try:
            return client.get_collection(name)
        except Exception:
            return client.create_collection(name)
    except Exception as e:
        logger.warning("chromadb_collection_failed", name=name, error=str(e))
        return None
