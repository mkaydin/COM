import json, re
from litellm import completion
from config import get_settings
from logging_config import get_logger
from agents.schemas import DetectiveResponse
from agents.prompts import DETECTIVE_SYSTEM_PROMPT
from agents.json_utils import schema_for_prompt, extract_json

logger = get_logger(__name__)
settings = get_settings()


def _extract_keywords(query: str) -> list[str]:
    stop = {"the", "what", "how", "does", "are", "and", "for", "with", "from",
            "that", "this", "their", "into", "between", "can", "you", "about",
            "main", "key", "effect", "affects", "affect", "factors", "role"}
    words = re.findall(r'[A-Za-z]{3,}', query.lower())
    keywords = [w for w in words if w not in stop]
    seen = []
    for w in keywords:
        if w not in seen:
            seen.append(w)
    return seen[:12]


async def _query_graph(query: str) -> list[dict]:
    keywords = _extract_keywords(query)
    if not keywords:
        return []

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
                WHERE ANY(kw IN $keywords WHERE
                    toLower(a.name) CONTAINS kw OR toLower(b.name) CONTAINS kw)
                RETURN a.name AS source, type(r) AS relation, b.name AS target,
                       coalesce(r.weight, 0.5) AS weight
                LIMIT 25
                """,
                keywords=keywords
            )
            records = []
            for record in result:
                records.append({
                    "source": record["source"],
                    "relation": record["relation"],
                    "target": record["target"],
                    "weight": record["weight"],
                })

            if records:
                names = list(set(r["source"] for r in records) | set(r["target"] for r in records))[:10]
                hop2 = session.run(
                    """
                    UNWIND $names AS name
                    MATCH (a:Entity {name: name})-[r]->(b:Entity)
                    WHERE NOT b.name IN $names
                    RETURN DISTINCT a.name AS source, type(r) AS relation,
                           b.name AS target, coalesce(r.weight, 0.5) AS weight
                    LIMIT 15
                    """,
                    names=names
                )
                for record in hop2:
                    entry = {
                        "source": record["source"],
                        "relation": record["relation"],
                        "target": record["target"],
                        "weight": record["weight"],
                    }
                    if entry not in records:
                        records.append(entry)

        driver.close()
        return records[:35]
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
        {"role": "system", "content": DETECTIVE_SYSTEM_PROMPT + schema_for_prompt(DetectiveResponse)},
        {"role": "user", "content": f"Analyze this query using graph traversal. Output DetectiveResponse JSON. Identify causal chains and hidden links.\n\nQUERY: {query}{context_block}"}
    ]

    try:
        from config import llm_complete
        response = await llm_complete(messages, temperature=0.6, max_tokens=4096, provider=provider)
        raw = response.choices[0].message.content
        _reasoning = getattr(response, 'reasoning', '')
        data = extract_json(raw)
        detective_output = DetectiveResponse(**data)
    except Exception as e:
        _reasoning = ""
        logger.error("detective_llm_failed", error=str(e))
        detective_output = DetectiveResponse(
            confidence=0.0,
            causal_chains=[],
            hidden_links=[],
            inconsistencies=[],
            verdict="Unable to complete graph analysis.",
        )

    logger.info("detective_complete", confidence=detective_output.confidence)
    _result = detective_output.model_dump()
    _result["_reasoning"] = _reasoning
    return {"detective_response": _result}
