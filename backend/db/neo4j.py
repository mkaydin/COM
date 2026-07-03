"""Neo4j client for graph storage and traversal."""
from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)
settings = get_settings()

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        try:
            from neo4j import GraphDatabase
            _driver = GraphDatabase.driver(
                settings.neo4j_url,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
            _driver.verify_connectivity()
            logger.info("neo4j_connected")
        except Exception as e:
            logger.warning("neo4j_unavailable", error=str(e))
    return _driver


def close_driver():
    global _driver
    if _driver:
        _driver.close()
        _driver = None
