from typing import Optional, List, Annotated, Literal, Any
from pydantic import BaseModel, Field, field_validator


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


class CausalChain(BaseModel):
    chain: List[str]
    evidence_strength: float = Field(ge=0.0, le=100.0)
    is_inferred: bool = False


class HiddenLink(BaseModel):
    entity_a: str
    entity_b: str
    relation: str


class OracleClaim(BaseModel):
    text: str
    sources: List[Any] = Field(default_factory=list)
    evidence_type: str = Field(default="documented", description="documented | inferred | graph-derived")


class OracleResponse(BaseModel):
    agent: str = Field(default="oracle")
    confidence: float = Field(ge=0.0, le=100.0)
    reasoning_type: str = Field(default="evidence_synthesis")
    claims: List[OracleClaim] = Field(default_factory=list)
    causal_chains: List[CausalChain] = Field(default_factory=list)
    hidden_links: List[HiddenLink] = Field(default_factory=list)
    verdict: str
    uncertainties: List[str] = Field(default_factory=list)
    evidence_sources: int = Field(default=0)
    graph_entities_found: int = Field(default=0)
    insufficient_data: bool = False


class SageAnalogy(BaseModel):
    domain: str
    concept: str
    application: str


class SageResponse(BaseModel):
    agent: str = Field(default="sage")
    confidence: float = Field(ge=0.0, le=100.0)
    reasoning_type: str = Field(default="synthesis")
    analogies: List[SageAnalogy] = Field(default_factory=list)
    synthesis: str
    ethical_dimensions: List[str] = Field(default_factory=list)
    speculation_warning: bool = False
    wiki_citations: List[str] = Field(default_factory=list)


class RagCitation(BaseModel):
    title: str = ""
    source_url: str = ""
    trust_tier: str = ""
    source_system: str = ""
    snippet: str = ""


class RagResponse(BaseModel):
    agent: str = Field(default="rag")
    confidence: float = Field(ge=0.0, le=100.0)
    reasoning_type: str = Field(default="evidence_retrieval")
    answer: str
    citations: List[RagCitation] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)
    trust_summary: str = ""
    insufficient_data: bool = False


class AgentComparison(BaseModel):
    confidence: float = 0.0
    type: str = ""
    strength: str = ""


class Conflict(BaseModel):
    topic: str
    status: str = Field(default="unresolved")
    severity: str = Field(default="nuance", description="factual (agents contradict on facts) | nuance (different angle/wording)")
    oracle: Optional[str] = None
    rag: Optional[str] = None
    sage: Optional[str] = None
    # legacy fields — retained only to read conflicts stored by older
    # versions, which named Scout/Historian/Detective instead of Oracle/Scholar.
    scout: Optional[str] = None
    historian: Optional[str] = None
    detective: Optional[str] = None

    @field_validator("oracle", "rag", "sage", "scout", "historian", "detective", mode="before")
    @classmethod
    def coerce_agent_field(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return str(v)

    @field_validator("severity", mode="before")
    @classmethod
    def coerce_severity(cls, v: Any) -> str:
        if v is None:
            return "nuance"
        s = str(v).strip().lower()
        return "factual" if s.startswith("fact") else "nuance"


class Agreement(BaseModel):
    topic: str
    agents: List[str] = Field(default_factory=list)

    @field_validator("agents", mode="before")
    @classmethod
    def coerce_agents(cls, v: Any) -> List[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v]
        return [str(v)]


class DecisionOption(BaseModel):
    label: str = Field(default="Strategic", description="Conservative | Exploratory | Strategic")
    action: str = Field(default="")
    risk: str = Field(default="medium", description="low | medium | high")

    @field_validator("label", mode="before")
    @classmethod
    def coerce_label(cls, v: Any) -> str:
        if isinstance(v, dict):
            return v.get("label", "Strategic")
        return str(v) if v else "Strategic"

    @field_validator("action", mode="before")
    @classmethod
    def coerce_action(cls, v: Any) -> str:
        if isinstance(v, dict):
            return v.get("action", "")
        return str(v) if v else ""

    @field_validator("risk", mode="before")
    @classmethod
    def coerce_risk(cls, v: Any) -> str:
        if isinstance(v, dict):
            return v.get("risk", "medium")
        return str(v) if v else "medium"


class ModeratorResponse(BaseModel):
    agent: str = Field(default="moderator")
    deliberation_id: Optional[str] = None
    comparison: dict = Field(default_factory=dict)
    conflicts: List[Conflict] = Field(default_factory=list)
    agreements: List[Agreement] = Field(default_factory=list)
    decision_space: List[DecisionOption] = Field(default_factory=list)
    alignment: str = Field(default="medium", description="high | medium | low — overall council agreement")

    @field_validator("alignment", mode="before")
    @classmethod
    def coerce_alignment(cls, v: Any) -> str:
        if v is None:
            return "medium"
        s = str(v).strip().lower()
        return s if s in ("high", "medium", "low") else "medium"

    @field_validator("decision_space", mode="before")
    @classmethod
    def coerce_decision_space(cls, v: Any) -> List[Any]:
        if isinstance(v, dict):
            return [{"label": k, **(val if isinstance(val, dict) else {"action": str(val)})} for k, val in v.items()]
        return v


class QueryClassification(BaseModel):
    query_type: Literal["factual", "relational", "strategic", "general"]
    confidence: float = Field(ge=0.0, le=100.0)
    reasoning: str


