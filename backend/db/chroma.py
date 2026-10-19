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


def _is_missing_collection(exc: Exception) -> bool:
    """Is this 'the collection does not exist', or is Chroma itself unwell?

    Chroma raises `NotFoundError` for a missing collection and something else —
    a connection error, an auth failure — when it cannot be reached. The old
    code treated every failure as "not there yet" and called `create_collection`
    anyway, which then also failed and was caught by the outer handler. Two
    silent failures stacked: an unreachable Chroma and a genuine miss were
    indistinguishable, and neither was ever logged.
    """
    try:
        from chromadb.errors import NotFoundError
    except Exception:
        NotFoundError = None

    # Both checks. The typed error is authoritative where it exists, but a
    # message match covers builds that raise something else for the same
    # condition — and a false negative here means the collection is never
    # created, so the safer direction is to treat "looks like a miss" as a miss.
    if NotFoundError is not None and isinstance(exc, NotFoundError):
        return True
    message = str(exc).lower()
    return "not found" in message or "does not exist" in message


def get_collection(name: str = "documents_shared"):
    """A collection, creating it on first use. `None` when Chroma is unusable.

    A missing collection is expected and silent. Anything else is logged with
    what actually went wrong, because that is the difference between "first run"
    and "the vector store is down and nobody will find out until search returns
    nothing".
    """
    global _collection
    client = get_client()
    if not client:
        logger.debug("chromadb_no_client", collection=name)
        return None

    try:
        return client.get_collection(name)
    except Exception as e:
        if not _is_missing_collection(e):
            logger.warning(
                "chromadb_collection_unreachable",
                collection=name,
                error=str(e)[:200],
                hint=(
                    "ChromaDB is reachable enough to answer but refused the "
                    "collection. Search over the vector store will return "
                    "nothing until this is resolved."
                ),
            )
            return None
        logger.info("chromadb_collection_absent_creating", collection=name)

    try:
        return client.create_collection(name)
    except Exception as e:
        logger.warning(
            "chromadb_collection_create_failed", collection=name, error=str(e)[:200]
        )
        return None
