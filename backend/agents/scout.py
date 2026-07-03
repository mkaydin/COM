import json, re
import asyncio
from litellm import completion
from config import get_settings
from logging_config import get_logger
from agents.schemas import ScoutResponse
from agents.prompts import SCOUT_SYSTEM_PROMPT
from agents.json_utils import schema_for_prompt, extract_json

logger = get_logger(__name__)
settings = get_settings()


async def scout_node(state: dict) -> dict:
    query = state.get("user_query", "")
    logger.info("scout_processing", query=query[:100])

    web_context = ""
    web_status = "llm_only"

    try:
        from agents.scout_websearch import scout_gather
        findings = await asyncio.wait_for(scout_gather(query), timeout=300)

        if findings.get("summaries"):
            web_context = "\n\nWEB INTELLIGENCE GATHERED:\n" + "\n---\n".join(
                f"Source: {s['source']}\nSummary: {s['summary']}"
                for s in findings["summaries"]
            )
            web_status = "web_scraped"
            logger.info("scout_web_gathered", sources=len(findings.get("sources", [])))
        elif findings.get("sources"):
            web_context = "\n\nWEB INTELLIGENCE: (sources found but content extraction yielded limited results)"
            web_status = "partial"
        else:
            web_context = "\n\nWEB INTELLIGENCE: (scraping returned no results — using LLM knowledge only)"
            web_status = "no_results"
    except asyncio.TimeoutError:
        logger.warning("scout_websearch_timeout")
        web_context = "\n\nWEB INTELLIGENCE: (web search timed out — using LLM knowledge only)"
        web_status = "timeout"
    except Exception as e:
        logger.warning("scout_websearch_failed", error=str(e))
        web_context = f"\n\nWEB INTELLIGENCE: (web search unavailable: {str(e)[:80]} — using LLM knowledge only)"
        web_status = "unavailable"

    provenance = "EXCLUSIVE web-scraped intelligence" if web_status == "web_scraped" else "LLM parametric knowledge (no live web sources)"

    # Select prompt based on provider
    provider = state.get("provider", "lm_studio")
    if provider == "lm_studio":
        from agents.prompts import LOCAL_SCOUT_PROMPT
        system_content = LOCAL_SCOUT_PROMPT
        max_tok = 8192
    else:
        system_content = SCOUT_SYSTEM_PROMPT + schema_for_prompt(ScoutResponse)
        max_tok = 8192

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"Research this topic.\n\nDATA: {provenance}\n\nQUERY: {query}{web_context}"}
    ]

    try:
        from config import llm_complete
        response = await llm_complete(messages, temperature=0.3, max_tokens=max_tok, provider=provider)
        raw = response.choices[0].message.content
        _reasoning = getattr(response, 'reasoning', '')
        data = extract_json(raw)

        if web_status in ("web_scraped", "partial"):
            data.setdefault("source_reliability", "high")
        elif web_status in ("no_results", "timeout", "unavailable"):
            existing_rel = data.get("source_reliability", "")
            if "fail" not in existing_rel.lower():
                data["source_reliability"] = f"medium ({web_status}: LLM knowledge only)"
            data["source_diversity"] = "low"

        if data.get("confidence", 0) > 1: data["confidence"] = data["confidence"] / 100.0
        scout_output = ScoutResponse(**data)
    except Exception as e:
        _reasoning = ""
        logger.error("scout_llm_failed", error=str(e))
        scout_output = ScoutResponse(
            confidence=0.0,
            topic=query[:100],
            key_signals=[],
            consensus="Unable to complete reconnaissance.",
            source_diversity="low",
            source_reliability="failed",
        )

    logger.info("scout_complete", confidence=scout_output.confidence)
    _result = scout_output.model_dump()
    _result["_reasoning"] = _reasoning
    return {"scout_response": _result}
