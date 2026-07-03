"""
GraphRAG pipeline — extracts entities and relationships from text, stores in Neo4j.
Used by the Detective agent for causal chain building and relationship traversal.
"""
import json
import re
from typing import List
from logging_config import get_logger
from config import get_settings

logger = get_logger(__name__)
settings = get_settings()


EXTRACTION_PROMPT = """You are a knowledge graph extractor. Given a text, extract ALL entities and relationships.

Output JSON only:
{
  "entities": [{"name": "EntityName", "type": "person|org|concept|tech|place|event"}],
  "relationships": [
    {"source": "EntityA", "target": "EntityB", "type": "causes|relates_to|contradicts|cites|part_of", "weight": 0.8}
  ]
}

Rules:
- Entity names should be normalized (canonical form).
- Relationship weight: 0.0-1.0 (1.0 = directly stated, 0.5 = inferred).
- Extract at least 3 entities if the text has meaningful content.
- Types: causes, relates_to, contradicts, cites, part_of, produces, uses, depends_on.
"""


async def extract_entities_relationships(text: str) -> dict:
    """Use LLM to extract entities and relationships from text."""
    from agents.json_utils import extract_json
    from config import llm_complete

    messages = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": f"Extract entities and relationships from:\n\n{text[:4000]}"},
    ]

    try:
        response = await llm_complete(messages, temperature=0.2, max_tokens=4096)
        raw = response.choices[0].message.content
        data = extract_json(raw)
        if data:
            return data
    except Exception as e:
        logger.warning("entity_extraction_failed", error=str(e))

    return {"entities": [], "relationships": []}


def store_in_neo4j(doc_id: str, title: str, extraction: dict):
    """Store extracted entities and relationships in Neo4j."""
    try:
        from db.neo4j import get_driver
        driver = get_driver()
        if not driver:
            logger.warning("neo4j_unavailable")
            return False

        entities = extraction.get("entities", [])
        relationships = extraction.get("relationships", [])

        with driver.session() as session:
            for entity in entities:
                session.run(
                    "MERGE (n:Entity {name: $name}) SET n.type = $type, n.source_doc = $doc",
                    name=entity["name"], type=entity.get("type", "concept"), doc=title,
                )

            for rel in relationships:
                session.run(
                    """
                    MATCH (a:Entity {name: $source}), (b:Entity {name: $target})
                    MERGE (a)-[r:RELATES_TO]->(b)
                    SET r.type = $type, r.weight = $weight, r.source_doc = $doc
                    """,
                    source=rel["source"], target=rel["target"],
                    type=rel.get("type", "relates_to"), weight=rel.get("weight", 0.5), doc=title,
                )

        logger.info("neo4j_stored", doc_id=doc_id, entities=len(entities), relationships=len(relationships))
        return True
    except Exception as e:
        logger.error("neo4j_store_failed", error=str(e))
        return False


async def ingest_to_graph(doc_id: str, title: str, content: str):
    """
    Full GraphRAG ingestion pipeline for the Detective:
    1. Extract entities + relationships via LLM
    2. Store in Neo4j
    """
    logger.info("graph_ingesting", doc_id=doc_id, title=title[:50])

    extraction = await extract_entities_relationships(content)
    logger.info("graph_extracted", entities=len(extraction.get("entities", [])), relationships=len(extraction.get("relationships", [])))

    store_in_neo4j(doc_id, title, extraction)

    return {
        "status": "graph_ingested",
        "entities": len(extraction.get("entities", [])),
        "relationships": len(extraction.get("relationships", [])),
    }
