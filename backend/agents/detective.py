import json, re
from litellm import completion
from config import get_settings
from logging_config import get_logger
from agents.schemas import DetectiveResponse
from agents.prompts import DETECTIVE_SYSTEM_PROMPT

logger = get_logger(__name__)
settings = get_settings()


async def _query_graph(query: str) -> list[dict]:
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            settings.neo4j_url,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        with driver.session() as session:
            result = session.run(
                """
                MATCH (a:Entity)-[r]->(b:Entity)
                WHERE a.name CONTAINS $query OR b.name CONTAINS $query
                RETURN a.name AS source, type(r) AS relation, b.name AS target, r.weight AS weight
                LIMIT 20
                """,
                query=query[:50]
            )
            records = []
            for record in result:
                records.append({
                    "source": record["source"],
                    "relation": record["relation"],
                    "target": record["target"],
                    "weight": record["weight"] or 0.5,
                })
        driver.close()
        return records
    except Exception as e:
        logger.warning("graph_traversal_unavailable", error=str(e))
        return []


async def detective_node(state: dict) -> dict:
    query = state.get("user_query", "")
    logger.info("detective_processing", query=query[:100])

    graph_data = await _query_graph(query)

    context_block = ""
    if graph_data:
        context_block = "\n\nGRAPH DATA:\n" + "\n".join(
            f"- ({r['source']})-[{r['relation']}]->({r['target']}) [weight: {r['weight']:.2f}]"
            for r in graph_data
        )
    else:
        context_block = "\n\nGRAPH DATA: (none available — Neo4j may be empty or unreachable)"

    messages = [
        {"role": "system", "content": DETECTIVE_SYSTEM_PROMPT},
        {"role": "user", "content": f"Analyze this query using graph traversal. Output DetectiveResponse JSON. Identify causal chains and hidden links.\n\nQUERY: {query}{context_block}"}
    ]

    try:
        response = completion(
            model=settings.llm_model,
            messages=messages,
            temperature=0.6,
            max_tokens=2048,
        )
        raw = response.choices[0].message.content

        json_match = re.search(r'\{[\s\S]*\}', raw)
        data = json.loads(json_match.group(0)) if json_match else {}
        detective_output = DetectiveResponse(**data)
    except Exception as e:
        logger.error("detective_llm_failed", error=str(e))
        detective_output = DetectiveResponse(
            confidence=0.0,
            causal_chains=[],
            hidden_links=[],
            inconsistencies=[],
            verdict="Unable to complete graph analysis.",
        )

    logger.info("detective_complete", confidence=detective_output.confidence)
    return {"detective_response": detective_output.model_dump()}
