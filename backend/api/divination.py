"""
Divination API — Scout-powered web intelligence with expanded search terms.

Flow:
  1. User submits a query
  2. LLM analyzes the query and generates 3-4 expanded search terms
  3. For each search term, Scout scrapes Wikipedia + summarizes
  4. LLM synthesizes all gathered intelligence with source attribution
  5. User can save the result to the Runic Library
"""
import json
import asyncio
import uuid
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from logging_config import get_logger
from config import get_settings, llm_complete, DIVINATION_PROVIDERS

router = APIRouter(prefix="/api/divination", tags=["divination"])
logger = get_logger(__name__)


class DivinationRequest(BaseModel):
    query: str
    provider: str = "glm"


class DivinationResult(BaseModel):
    original_query: str
    search_terms: List[str]
    sources: List[dict]
    synthesis: str
    summaries: List[dict] = []


class DivinationSaveRequest(BaseModel):
    title: str
    synthesis: str
    sources: List[dict] = []
    summaries: List[dict] = []


async def _with_provider(provider: str, coro_fn, *args, **kwargs):
    """Run a coroutine with a temporarily switched provider, then restore."""
    s = get_settings()
    original = s.active_provider

    if provider != original and provider in DIVINATION_PROVIDERS:
        p = DIVINATION_PROVIDERS[provider]
        s.llm_model = p["llm_model"]
        s.llm_api_key = p["llm_api_key"]
        s.llm_api_base = p["llm_api_base"]
        s.supports_json_mode = p["supports_json_mode"]
        import os
        os.environ["OPENAI_API_KEY"] = p["llm_api_key"]
        os.environ["OPENAI_API_BASE"] = p["llm_api_base"]
        logger.info(f"divination_provider_temp provider={provider}")

    try:
        return await coro_fn(*args, **kwargs)
    finally:
        if provider != original:
            orig_p = DIVINATION_PROVIDERS.get(original, DIVINATION_PROVIDERS["glm"])
            s.llm_model = orig_p["llm_model"]
            s.llm_api_key = orig_p["llm_api_key"]
            s.llm_api_base = orig_p["llm_api_base"]
            s.supports_json_mode = orig_p["supports_json_mode"]
            import os
            os.environ["OPENAI_API_KEY"] = orig_p["llm_api_key"]
            os.environ["OPENAI_API_BASE"] = orig_p["llm_api_base"]


async def _expand_search_terms(query: str, provider: str) -> List[str]:
    """Use LLM to analyze a query and generate 3-4 expanded search terms."""
    messages = [
        {"role": "system", "content": (
            "Generate 3-4 diverse search terms for this query. Reply ONLY with JSON: "
            "{\"terms\": [\"term 1\", \"term 2\", \"term 3\", \"term 4\"]}"
        )},
        {"role": "user", "content": f"Query: {query}"}
    ]

    try:
        response = await _with_provider(provider, llm_complete, messages, temperature=0.4, max_tokens=2048)
        raw = response.choices[0].message.content
        from agents.json_utils import extract_json
        data = extract_json(raw)
        terms = data.get("terms", [])
        return terms[:4] if terms else [query]
    except Exception as e:
        logger.warning("search_term_expansion_failed", error=str(e))
        return [query]


async def _gather_intelligence(search_terms: List[str]) -> tuple:
    """Run Scout's web search pipeline for all search terms in parallel."""
    from agents.scout_websearch import scout_gather

    async def _gather_one(term):
        try:
            return await scout_gather(term)
        except Exception as e:
            logger.warning("divination_gather_failed", term=term, error=str(e))
            return {"sources": [], "summaries": []}

    # Run all searches in parallel
    results = await asyncio.gather(*[_gather_one(t) for t in search_terms])

    all_sources = []
    all_summary_dicts = []
    all_summary_strings = []

    for term, findings in zip(search_terms, results):
        for src in findings.get("sources", []):
            all_sources.append({
                "url": src.get("url", ""),
                "title": src.get("title", ""),
                "type": src.get("type", "web"),
                "search_term": term,
            })
        for summary in findings.get("summaries", []):
            all_summary_dicts.append({
                "source": summary.get("source", ""),
                "text": summary.get("summary", ""),
                "search_term": term,
            })
            all_summary_strings.append(f"[{summary.get('source', '')}] {summary.get('summary', '')}")

    return all_sources, all_summary_dicts, all_summary_strings


def _json_to_markdown(text: str) -> str:
    """If the LLM wrapped its response in JSON, convert it to readable markdown."""
    from agents.json_utils import extract_json

    try:
        data = extract_json(text)
    except Exception:
        return text

    if not data or len(text) < 50:
        return text

    # Simple: {"answer": "..."} or {"result": "..."} or {"response": "..."}
    for key in ("answer", "result", "response", "analysis", "content", "text"):
        val = data.get(key)
        if isinstance(val, str) and len(val) > 50:
            return val

    # Structured: {"title": "...", "sections": [...]} or {"analysis": {...}}
    def _flatten(obj, depth=0):
        lines = []
        if isinstance(obj, dict):
            title = obj.get("title") or obj.get("name") or obj.get("heading")
            if title and depth == 0:
                lines.append(f"# {title}")
            elif title:
                lines.append(f"{'#' * (depth + 2)} {title}")

            content = obj.get("content") or obj.get("text") or obj.get("description") or obj.get("value")
            if content and isinstance(content, str):
                lines.append(content)

            sections = obj.get("sections") or obj.get("parts") or obj.get("items")
            if isinstance(sections, list):
                for s in sections:
                    lines.append(_flatten(s, depth + 1))

            conclusion = obj.get("conclusion") or obj.get("summary")
            if conclusion and isinstance(conclusion, str):
                lines.append(f"\n## Conclusion\n{conclusion}")

            # Handle nested analysis object
            for k, v in obj.items():
                if k not in ("title", "name", "heading", "content", "text", "description",
                             "value", "sections", "parts", "items", "conclusion", "summary") and isinstance(v, (dict, list)):
                    lines.append(_flatten(v, depth + 1))
        elif isinstance(obj, list):
            for item in obj:
                lines.append(_flatten(item, depth))
        elif isinstance(obj, str) and len(obj) > 20:
            lines.append(obj)

        return "\n\n".join(l for l in lines if l)

    flattened = _flatten(data)
    if len(flattened) > 100:
        return flattened

    return text


async def _synthesize(query: str, search_terms: List[str], summaries: List[str], provider: str) -> str:
    """Use LLM to synthesize gathered intelligence into a comprehensive response."""
    context = "\n\n".join(summaries) if summaries else "(No web sources found — using parametric knowledge)"

    messages = [
        {"role": "system", "content": (
            "You are Scout, an intelligence analyst. Synthesize gathered intelligence into a "
            "comprehensive, well-structured report. Reference sources by [Source: title] inline. "
            "If web data is limited, supplement with your knowledge and mark it as '[General Knowledge]'."
        )},
        {"role": "user", "content": (
            f"ORIGINAL QUERY: {query}\n\n"
            f"SEARCH TERMS USED: {', '.join(search_terms)}\n\n"
            f"GATHERED INTELLIGENCE:\n{context}\n\n"
            f"Write a MARKDOWN report with two sections:\n\n"
            f"1. ## Source Summaries — For each source, a detailed paragraph covering key findings.\n"
            f"2. ## Analysis — A detailed, thorough explanation answering the query. "
            f"Synthesize across sources, draw connections, highlight patterns.\n\n"
            f"Use ## headings and bullet points. Cite sources inline. No JSON wrapping."
        )}
    ]

    try:
        response = await _with_provider(provider, llm_complete, messages, temperature=0.4, max_tokens=16384)
        content = response.choices[0].message.content or "Analysis unavailable."
        content = _json_to_markdown(content)
        return content
    except Exception as e:
        logger.error("divination_synthesis_failed", error=str(e))
        return f"Synthesis failed: {str(e)}"


@router.post("/search")
async def divination_search(req: DivinationRequest):
    """Main divination endpoint: expand → search → synthesize."""
    logger.info("divination_start", query=req.query[:80], provider=req.provider)

    # Step 1: Expand search terms
    search_terms = await _expand_search_terms(req.query, req.provider)

    # Step 2: Gather intelligence
    sources, summaries_list, summaries = await _gather_intelligence(search_terms)

    # Step 3: Synthesize
    synthesis = await _synthesize(req.query, search_terms, summaries, req.provider)

    return DivinationResult(
        original_query=req.query,
        search_terms=search_terms,
        sources=sources,
        synthesis=synthesis,
        summaries=summaries_list,
    )


@router.post("/save")
async def divination_save(req: DivinationSaveRequest, background_tasks: BackgroundTasks):
    """Save divination results to the Runic Library as a document."""

    sources_section = "\n".join(
        f"- [{s.get('title', 'Unknown')}]({s.get('url', '')})"
        for s in req.sources
    )

    summaries_section = "\n\n".join(
        f"### {s.get('source', 'Unknown')}\n{s.get('text', '')}"
        for s in req.summaries
    )

    full_content = f"""# Divination Report: {req.title}

## Analysis
{req.synthesis}

## Sources
{sources_section or 'No web sources collected'}

## Source Summaries
{summaries_section or 'No summaries available'}
"""

    from db.postgres import async_session
    from db.models import Document
    from datetime import datetime
    import uuid as uuid_mod

    doc = Document(
        id=uuid_mod.uuid4(),
        title=f"[Divination] {req.title[:200]}",
        content=full_content[:100000],
        source_type="divination",
        metadata_json={"type": "divination", "sources": req.sources, "summaries": req.summaries},
    )

    async with async_session() as db:
        db.add(doc)
        await db.commit()

    # Background ingestion into ChromaDB
    async def _ingest():
        try:
            from ingestion.embedder import ingest_document
            await ingest_document(str(doc.id), doc.title, full_content, "divination")
            logger.info("divination_ingested", doc_id=str(doc.id))
        except Exception as e:
            logger.error("divination_ingest_failed", error=str(e))

    background_tasks.add_task(_ingest)

    return {"status": "saved", "id": str(doc.id), "title": doc.title}
