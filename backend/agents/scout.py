"""
Scout Agent — Web Intelligence Gatherer

Exploratory intelligence operative. Searches the web, detects emerging signals,
identifies contradictions, and delivers compressed tactical reports.

Pipeline: Query → Web Search → Scrape → Filter → Signal Extraction → Report

See: PLAN.md Section 3.2 (Scout), WEBSEARCH.md, character_persona.md
"""

from langgraph.graph import StateGraph
from pydantic import BaseModel, Field
from typing import Optional, List
from litellm import completion
from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)
settings = get_settings()


class ScoutSignal(BaseModel):
    signal: str
    strength: str = Field(description="strong | moderate | emerging")
    sources: int = Field(ge=1)


class ConflictFinding(BaseModel):
    topic: str
    claim_a: str
    claim_b: str
    resolution: str = Field(default="unresolved")


class TemporalNote(BaseModel):
    finding: str
    status: str = Field(description="outdated | evolving | current")
    current: Optional[str] = None


class SourceCitation(BaseModel):
    url: str
    type: str = Field(description="article | forum | github | paper | news | docs")
    recency: Optional[str] = None
    reliability: str = Field(description="high | medium | low | unverified")


class ScoutResponse(BaseModel):
    agent: str = Field(default="scout")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_type: str = Field(default="reconnaissance")
    topic: str
    key_signals: List[ScoutSignal]
    consensus: str
    conflicting_information: List[ConflictFinding] = Field(default_factory=list)
    temporal_notes: List[TemporalNote] = Field(default_factory=list)
    source_diversity: str = Field(description="high | medium | low")
    source_reliability: str
    weak_signals: List[str] = Field(default_factory=list)
    sources: List[SourceCitation] = Field(default_factory=list)


SCOUT_SYSTEM_PROMPT = """You are SCOUT — an exploratory intelligence operative for the Council of Minds.

Your job: search the web, detect emerging signals, identify contradictions, and deliver
compressed tactical intelligence reports.

CORE PHILOSOPHY:
- Truth is probabilistic and evolves over time.
- Sources conflict, narratives shift, trends emerge before facts stabilize.
- Track consensus, measure uncertainty, distinguish signal from noise.

TONE: concise, tactical, information-dense. Like an OSINT researcher. No emotional language.

OUTPUT RULES:
- Separate facts from speculation.
- Indicate confidence per finding.
- Mention uncertainty explicitly.
- Prefer multiple independent confirmations before reporting consensus.
- Identify outdated information (temporal awareness).
- Compress large information spaces into actionable summaries.
- NEVER present rumors as facts.
- NEVER over-trust a single source.
- NEVER fabricate consensus or hallucinate citations.
- NEVER become philosophical or theorize causal structures (that's Detective/Sage's job).

OUTPUT FORMAT: Structured JSON matching the ScoutResponse schema:
{
  "agent": "scout",
  "confidence": 0.74,
  "reasoning_type": "reconnaissance",
  "topic": "...",
  "key_signals": [{"signal": "...", "strength": "strong|moderate|emerging", "sources": N}],
  "consensus": "...",
  "conflicting_information": [{"topic": "...", "claim_a": "...", "claim_b": "...", "resolution": "unresolved"}],
  "temporal_notes": [{"finding": "...", "status": "outdated|evolving|current", "current": "..."}],
  "source_diversity": "high|medium|low",
  "source_reliability": "...",
  "weak_signals": ["..."],
  "sources": [{"url": "...", "type": "...", "recency": "...", "reliability": "high|medium|low|unverified"}]
}
"""


async def scout_node(state: dict) -> dict:
    """
    Scout LangGraph node.
    
    Receives the user query, optionally performs web search/scraping,
    and returns a structured intelligence report.
    
    Phase 2: integrates with scout_websearch.py for actual web scraping.
    Phase 1 (current): uses LLM-only reasoning with a placeholder response.
    """
    query = state.get("user_query", "")
    logger.info("scout_processing", query=query[:100])

    messages = [
        {"role": "system", "content": SCOUT_SYSTEM_PROMPT},
        {"role": "user", "content": f"Research this topic and produce an intelligence report: {query}"}
    ]

    try:
        response = completion(
            model=settings.llm_model,
            messages=messages,
            temperature=0.3,
            max_tokens=2048,
        )
        raw = response.choices[0].message.content

        # Parse JSON from response (handles markdown-wrapped JSON)
        import json, re
        json_match = re.search(r'\{[\s\S]*\}', raw)
        data = json.loads(json_match.group(0)) if json_match else {}
        scout_output = ScoutResponse(**data)
    except Exception as e:
        logger.error("scout_llm_failed", error=str(e))
        scout_output = ScoutResponse(
            confidence=0.0,
            topic=query[:100],
            key_signals=[],
            consensus="Unable to complete reconnaissance.",
            source_diversity="low",
            source_reliability="failed",
            sources=[]
        )

    logger.info("scout_complete", confidence=scout_output.confidence)

    return {"scout_response": scout_output.model_dump()}
