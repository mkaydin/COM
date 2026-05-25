from typing import Optional, List, Annotated, Literal
from pydantic import BaseModel, Field
from langgraph.graph.message import add_messages


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


class HistorianClaim(BaseModel):
    text: str
    sources: List[dict] = Field(default_factory=list)


class HistorianResponse(BaseModel):
    agent: str = Field(default="historian")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_type: str = Field(default="evidence")
    claims: List[HistorianClaim]
    verdict: str
    uncertainties: List[str] = Field(default_factory=list)
    insufficient_data: bool = False


class CausalChain(BaseModel):
    chain: List[str]
    evidence_strength: float = Field(ge=0.0, le=1.0)
    is_inferred: bool = False


class HiddenLink(BaseModel):
    entity_a: str
    entity_b: str
    relation: str


class DetectiveResponse(BaseModel):
    agent: str = Field(default="detective")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_type: str = Field(default="inference")
    causal_chains: List[CausalChain] = Field(default_factory=list)
    hidden_links: List[HiddenLink] = Field(default_factory=list)
    inconsistencies: List[str] = Field(default_factory=list)
    verdict: str


class SageAnalogy(BaseModel):
    domain: str
    concept: str
    application: str


class SageResponse(BaseModel):
    agent: str = Field(default="sage")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_type: str = Field(default="synthesis")
    analogies: List[SageAnalogy] = Field(default_factory=list)
    synthesis: str
    ethical_dimensions: List[str] = Field(default_factory=list)
    speculation_warning: bool = False
    wiki_citations: List[str] = Field(default_factory=list)


class AgentComparison(BaseModel):
    confidence: float
    type: str
    strength: str


class Conflict(BaseModel):
    topic: str
    status: str = Field(default="unresolved")
    scout: Optional[str] = None
    historian: Optional[str] = None
    detective: Optional[str] = None
    sage: Optional[str] = None


class Agreement(BaseModel):
    topic: str
    agents: List[str]


class DecisionOption(BaseModel):
    label: str = Field(description="Conservative | Exploratory | Strategic")
    action: str
    risk: str = Field(description="low | medium | high")


class ModeratorResponse(BaseModel):
    agent: str = Field(default="moderator")
    deliberation_id: Optional[str] = None
    comparison: dict = Field(default_factory=dict)
    conflicts: List[Conflict] = Field(default_factory=list)
    agreements: List[Agreement] = Field(default_factory=list)
    decision_space: List[DecisionOption] = Field(default_factory=list)


class TrustGuidance(BaseModel):
    agent: str
    trust_level: str = Field(description="high | medium | low")
    reason: str


class CompanionResponse(BaseModel):
    agent: str = Field(default="companion")
    deliberation_id: Optional[str] = None
    translation: str
    trust_guidance: List[TrustGuidance] = Field(default_factory=list)
    clarifying_questions: List[str] = Field(default_factory=list)
    summary: str
    recommended_agent: str
    recommendation_reason: str


class QueryClassification(BaseModel):
    query_type: Literal["factual", "relational", "strategic", "general"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


ALL_RESPONSE_SCHEMAS = {
    "scout": ScoutResponse,
    "historian": HistorianResponse,
    "detective": DetectiveResponse,
    "sage": SageResponse,
    "moderator": ModeratorResponse,
    "companion": CompanionResponse,
}
