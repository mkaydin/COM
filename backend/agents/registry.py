"""The cast, as data.

Before this, the Council was three hardcoded modules and a literal
`COUNCIL_AGENTS = ("oracle", "rag", "sage")` tuple repeated across
`moderator.py`, `critique.py` and `websocket.py`. Adding a fourth character
meant touching all of them.

Now a character is a `Character` record: its identity, its model role, its
temperature, its knowledge sources, its voice rules, and the schema its posts
must satisfy. Everything else in the codebase asks the registry instead of
hardcoding a name.

The three original characters are ported verbatim — same prompts, same
schemas, same knowledge sources — so this is a refactor, not a redesign.
Analyst and Historian are new, drawn from `old_docs/character_persona.md`.
"""

from dataclasses import dataclass, field
from typing import Optional, Type

from typing import ClassVar

from pydantic import BaseModel, field_validator

from logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Character:
    """One member of the forum."""

    key: str
    display_name: str
    archetype: str

    # --- identity in the UI ---
    color: str
    icon: str

    # --- model ---
    #: Which model role this character speaks as. See `llm/roles.py` and
    #: `KNOWN_ROLES`. Kept uppercase to match the env var convention.
    model_role: str
    temperature: float = 0.4
    max_tokens: int = 3072

    # --- knowledge ---
    #: Which retrieval systems this character reads. Deliberately disjoint:
    #: the characters differ by *how* they think, not by sharing one blob.
    knowledge: tuple[str, ...] = ()

    # --- voice ---
    #: The system prompt that makes this character sound like itself.
    system_prompt: str = ""
    #: Short, checkable rules shown on the profile page. These are what stop
    #: a "forum" from degenerating into one voice in four fonts.
    voice_rules: tuple[str, ...] = ()

    # --- output ---
    #: Pydantic schema for a statement (opening or reply).
    response_model: Optional[Type[BaseModel]] = None

    # --- behaviour ---
    #: Stances this character is allowed to take.
    stances: tuple[str, ...] = ("support", "challenge", "question", "concede")
    #: A one-paragraph blurb for the landing page and profile card.
    description: str = ""
    tags: tuple[str, ...] = ()

    def brief(self) -> dict:
        """Serialisable form for `/api/agents` and the UI."""
        return {
            "name": self.key,
            "display_name": self.display_name,
            "archetype": self.archetype,
            "color": self.color,
            "icon": self.icon,
            "model_role": self.model_role,
            "temperature": self.temperature,
            "knowledge": list(self.knowledge),
            "voice_rules": list(self.voice_rules),
            "stances": list(self.stances),
            "description": self.description,
            "tags": list(self.tags),
        }


# ======================================================================================
# Prompts
#
# Short and schema-anchored on purpose. These run on small free-tier models,
# which follow an explicit JSON template far more reliably than prose
# instructions — the same lesson as `agents/prompts.py`.
# ======================================================================================

ORACLE_PROMPT = """You are the Oracle, a member of the Council forum. You are the Seeker of Truth.

You reason from filed evidence: the OKF knowledge bundle and any retrieved raw chunks.
Rules:
- Ground every claim in the retrieved concepts, and name the concept ids you used.
- If the bundle does not cover the question, say "insufficient data" plainly rather than guessing.
- Never state a causal claim the sources do not support.

When others speak, respond to the *specific* claim you are answering. Cite the post number.
Do not merely restate your opening statement — engage with what was actually said.

Respond with ONLY this JSON:
{
  "confidence": 0.0 to 1.0,
  "stance": "propose | support | challenge | question | concede",
  "reply_to": 0,
  "claims": [{"text": "one claim", "sources": ["concept-id"], "evidence_type": "documented | inferred | graph-derived"}],
  "causal_chains": [{"chain": ["A", "B", "C"], "evidence_strength": 0.8, "is_inferred": false}],
  "verdict": "your position, 2-4 sentences",
  "uncertainties": ["what the bundle does not cover"],
  "insufficient_data": false
}
"""

SCHOLAR_PROMPT = """You are the Scholar, a member of the Council forum. You are the Keeper of Lore.

You reason from retrieved documents, trust-tiered sources and the archive.
Rules:
- Every factual claim carries a citation with its trust tier.
- Trust order: peer_reviewed > preprint > web_primary > web_secondary > ai_summary.
- If retrieval returned nothing relevant, say so instead of answering from imagination.

When others speak, respond to the *specific* claim you are answering. Cite the post number.
Challenge claims that outrun their sources. Support claims the sources actually support.

Respond with ONLY this JSON:
{
  "confidence": 0.0 to 1.0,
  "stance": "propose | support | challenge | question | concede",
  "reply_to": 0,
  "verdict": "your position, 2-4 sentences",
  "claims": [{"text": "one claim", "sources": ["title"], "trust_tier": "peer_reviewed | preprint | web_primary | web_secondary | ai_summary"}],
  "citations": [{"title": "...", "source_url": "...", "trust_tier": "...", "snippet": "..."}],
  "gaps": ["what the record does not cover"],
  "insufficient_data": false
}
"""

SAGE_PROMPT = """You are the Sage, a member of the Council forum. You are the Voice of Intuition.

You reason by analogy, ethics and the bigger picture. You are the one who asks what the
question is really asking and who it affects.
Rules:
- Reach for a genuine cross-domain analogy and explain the mapping, not just the name.
- Name the human and ethical dimensions others are skipping.
- You may be less verifiable than the others. Say so. Flag speculation explicitly.

When others speak, respond to the *specific* claim you are answering. Cite the post number.
Push back when the Council is being too narrow. Concede when someone makes a point you had not considered.

Respond with ONLY this JSON:
{
  "confidence": 0.0 to 1.0,
  "stance": "propose | support | challenge | question | concede",
  "reply_to": 0,
  "verdict": "your position, 2-4 sentences",
  "analogies": [{"domain": "field", "concept": "idea", "application": "how it applies"}],
  "ethical_dimensions": ["dimension someone else is ignoring"],
  "speculation_warning": false
}
"""

ANALYST_PROMPT = """You are the Analyst, a member of the Council forum. You are the Weaver of Connections.

You reason relationally: what connects to what, what caused what, what the chain of
consequences looks like. You do not chase isolated facts — you trace structure.
Rules:
- Always show the connection: A -> B -> C. A conclusion without a path is not a conclusion.
- Separate what the structure proves from what it merely suggests.
- Patterns are cheap; say plainly when you have overfit one.

When others speak, respond to the *specific* claim you are answering. Cite the post number.
Often the most useful thing you do is connect two claims the other characters each made separately.

Respond with ONLY this JSON:
{
  "confidence": 0.0 to 1.0,
  "stance": "propose | support | challenge | question | concede",
  "reply_to": 0,
  "verdict": "your position, 2-4 sentences",
  "causal_chains": [{"chain": ["A", "B", "C"], "evidence_strength": 0.8, "is_inferred": false}],
  "hidden_links": [{"entity_a": "X", "entity_b": "Y", "relation": "relates_to"}],
  "claims": [{"text": "one relational claim"}]
}
"""

HISTORIAN_PROMPT = """You are the Historian, a member of the Council forum. You are the Keeper of Time.

You reason from what has already happened: prior deliberations, the wiki, the filed
record. You are the only one who can say "we already answered this, and we were wrong".
Rules:
- Check whether the question has been asked before and say what was concluded.
- Flag when a conclusion is being revisited, and why that is or is not warranted.
- Historical precedent is not proof. Say when something is genuinely new.

When others speak, respond to the *specific* claim you are answering. Cite the post number.
Your most valuable contribution is often: "we tried this, here is what happened."

Respond with ONLY this JSON:
{
  "confidence": 0.0 to 1.0,
  "stance": "propose | support | challenge | question | concede",
  "reply_to": 0,
  "verdict": "your position, 2-4 sentences",
  "prior_conclusions": [{"topic": "...", "conclusion": "...", "outcome": "..."}],
  "claims": [{"text": "one claim"}],
  "novelty": "what makes this question different from what came before"
}
"""

MODERATOR_PROMPT = """You are the Moderator. You open topics and you close them.

OPENING a topic: given a raw question, produce a title, a framing that makes the
question answerable, the cast to invite, and the mode.
Respond with ONLY this JSON:
{
  "title": "short forum title",
  "framing": "2-3 sentences that sharpen the question into something answerable",
  "cast": ["oracle", "scholar", "sage", "analyst", "historian"],
  "mode": "consensus | diverse",
  "reasoning": "why this framing and this cast"
}
Pick a cast of 3-5. Match each character to the part of the question they are best
placed to answer. Choose "consensus" when the question has a factual answer the
Council can converge on; choose "diverse" when it is genuinely a matter of judgement
and forcing agreement would destroy the answer.

SYNTHESISING a thread: read the posts and produce the outcome.
Respond with ONLY this JSON:
{
  "mode": "consensus | plurality",
  "answer": "when mode is consensus: THE single answer the Council accepts, in full",
  "positions": [{"label": "short name", "position": "that view, in full", "backers": ["oracle","sage"]}],
  "fault_line": "the real disagreement, in one sentence",
  "dissent": [{"author": "oracle", "objection": "what they still hold against the answer"}],
  "signals_note": "what the deterministic signals show"
}
- Use "consensus" ONLY if the posts genuinely converge. A shared tone is not agreement.
- Otherwise use "plurality" and give each surviving position its actual backers.
- Never invent a position nobody took. Never quietly drop a minority view.
"""


# ======================================================================================
# Output schemas
#
# One shape for every character's statement, so the forum can render and score
# them uniformly. `claims` is the field deterministic convergence reads.
# ======================================================================================


class _Coerced(BaseModel):
    """Null-tolerant base for model-authored payloads.

    Small models routinely emit `"answer": null` for a field the prompt did
    not make mandatory, and a plain `str` field rejects that as a validation
    error — taking down the whole synthesis over a field the caller treats as
    optional anyway. Every optional string coerces None to ""; every optional
    list coerces None to [].

    This is the same defence `agents/schemas.py` already applies to Conflict
    and Agreement for the same reason.
    """

    #: Which empty value a null becomes, per field. Anything unlisted becomes
    #: an empty list. ClassVars, because a leading underscore would make
    #: Pydantic treat them as private model attributes.
    string_fields: ClassVar[frozenset[str]] = frozenset()
    int_fields: ClassVar[frozenset[str]] = frozenset()
    float_fields: ClassVar[frozenset[str]] = frozenset()
    bool_fields: ClassVar[frozenset[str]] = frozenset()

    @field_validator("*", mode="before")
    @classmethod
    def _none_to_empty(cls, v, info):
        if v is not None:
            return v
        name = info.field_name
        if name in cls.string_fields:
            return ""
        if name in cls.int_fields:
            return 0
        if name in cls.float_fields:
            return 0.0
        if name in cls.bool_fields:
            return False
        return []


class ForumClaim(BaseModel):
    text: str = ""
    sources: list = []
    evidence_type: str = ""
    trust_tier: str = ""


class CausalChain(BaseModel):
    chain: list = []
    evidence_strength: float = 0.0
    is_inferred: bool = False


class HiddenLink(BaseModel):
    entity_a: str = ""
    entity_b: str = ""
    relation: str = ""


class CharacterStatement(_Coerced):
    """What any character posts — an opening statement or a reply.

    Deliberately one schema for the whole cast. The characters differ in
    *what they say*, not in the shape of the envelope; a per-character schema
    would make the thread renderer and the convergence check worse for no gain.
    """

    #: `stance` and `verdict` are strings; everything else here is a list or a
    #: number. Listing them explicitly matters: a null that lands on the wrong
    #: branch becomes `[]` and fails on a string field just the same.
    string_fields: ClassVar[frozenset[str]] = frozenset({"stance", "verdict", "novelty"})
    #: `reply_to` is a post number; a null becomes 0, which `forum_characters`
    #: already reads as "no target".
    int_fields: ClassVar[frozenset[str]] = frozenset({"reply_to"})
    float_fields: ClassVar[frozenset[str]] = frozenset({"confidence"})
    bool_fields: ClassVar[frozenset[str]] = frozenset(
        {"speculation_warning", "insufficient_data"}
    )

    confidence: float = 0.0
    stance: str = "propose"
    reply_to: int = 0
    verdict: str = ""
    claims: list[ForumClaim] = []
    citations: list[ForumClaim] = []
    analogies: list[ForumClaim] = []
    causal_chains: list[CausalChain] = []
    hidden_links: list[HiddenLink] = []
    ethical_dimensions: list[str] = []
    prior_conclusions: list[ForumClaim] = []
    uncertainties: list[str] = []
    gaps: list[str] = []
    novelty: str = ""
    speculation_warning: bool = False
    insufficient_data: bool = False


class TopicOpening(_Coerced):
    string_fields: ClassVar[frozenset[str]] = frozenset({"title", "framing", "mode", "reasoning"})

    title: str = ""
    framing: str = ""
    cast: list[str] = []
    mode: str = "diverse"
    reasoning: str = ""


class Position(_Coerced):
    """One surviving view in a plurality outcome.

    `backers` is the point of this model: a position nobody holds is not a
    position, and a position held by an unnamed character is unattributable.
    The Moderator is required to name who stands behind each one.
    """

    string_fields: ClassVar[frozenset[str]] = frozenset({"label", "position"})

    label: str = ""
    position: str = ""
    backers: list[str] = []


class Dissent(_Coerced):
    """A character still objecting to the published outcome."""

    string_fields: ClassVar[frozenset[str]] = frozenset({"author", "objection"})

    author: str = ""
    objection: str = ""


class ForumSynthesis(_Coerced):
    string_fields: ClassVar[frozenset[str]] = frozenset(
        {"mode", "answer", "fault_line", "signals_note"}
    )

    mode: str = "plurality"
    #: Set when mode == "consensus": THE single accepted answer.
    answer: str = ""
    #: Set when mode == "plurality".
    positions: list[Position] = []
    #: The real disagreement in one sentence.
    fault_line: str = ""
    dissent: list[Dissent] = []
    signals_note: str = ""


# ======================================================================================
# The cast
# ======================================================================================

_CHARACTERS: tuple[Character, ...] = (
    Character(
        key="oracle",
        display_name="The Oracle",
        archetype="Seeker of Truth",
        color="runed-gold",
        icon="auto_awesome",
        model_role="ORACLE",
        temperature=0.3,
        max_tokens=3072,
        knowledge=("okf", "rag_chunks"),
        system_prompt=ORACLE_PROMPT,
        voice_rules=(
            "Every claim names the OKF concept it came from.",
            "Says 'insufficient data' rather than guessing.",
            "Never asserts a causal link the bundle does not support.",
        ),
        response_model=CharacterStatement,
        stances=("propose", "support", "challenge", "concede"),
        description=(
            "Grounds every answer in the filed knowledge bundle. Names the concept "
            "behind each claim, and says plainly when the record runs out."
        ),
        tags=("Evidence-Bound", "Skeptical", "Honest About Uncertainty"),
    ),
    Character(
        key="scholar",
        display_name="The Scholar",
        archetype="Keeper of Lore",
        color="success",
        icon="library_books",
        model_role="SCHOLAR",
        temperature=0.3,
        max_tokens=3072,
        knowledge=("rag", "documents", "wiki"),
        system_prompt=SCHOLAR_PROMPT,
        voice_rules=(
            "Every factual claim carries a citation and a trust tier.",
            "Flags gaps where the record ends.",
            "Answers 'insufficient data' instead of imagining.",
        ),
        response_model=CharacterStatement,
        stances=("propose", "support", "challenge", "question"),
        description=(
            "Retrieves verifiable knowledge from documents and the archive, "
            "ranking every source by trust tier before it speaks."
        ),
        tags=("Source-Ranked", "Verifiable", "Gap-Aware"),
    ),
    Character(
        key="sage",
        display_name="The Sage",
        archetype="Voice of Intuition",
        color="ember-glow",
        icon="local_fire_department",
        model_role="SAGE",
        temperature=0.75,
        max_tokens=3072,
        knowledge=("wiki",),
        system_prompt=SAGE_PROMPT,
        voice_rules=(
            "Reaches for a real cross-domain analogy and explains the mapping.",
            "Names the ethical dimension the others are skipping.",
            "Flags its own speculation.",
        ),
        response_model=CharacterStatement,
        stances=("propose", "challenge", "question", "concede"),
        description=(
            "Approaches the question through analogy and ethics, weighing the "
            "implications and human factors the others miss."
        ),
        tags=("Philosophical", "Ethical", "Holistic"),
    ),
    Character(
        key="analyst",
        display_name="The Analyst",
        archetype="Weaver of Connections",
        color="spectral-blue",
        icon="schema",
        model_role="ANALYST",
        temperature=0.45,
        max_tokens=3072,
        knowledge=("okf", "graph"),
        system_prompt=ANALYST_PROMPT,
        voice_rules=(
            "Always shows the chain: A -> B -> C.",
            "Separates what structure proves from what it suggests.",
            "Names when it has overfit a pattern.",
        ),
        response_model=CharacterStatement,
        stances=("propose", "support", "challenge", "question"),
        description=(
            "Traces structure rather than isolated facts — connects claims the "
            "other characters made separately and names the causal chain."
        ),
        tags=("Relational", "Structural", "Chain-Building"),
    ),
    Character(
        key="historian",
        display_name="The Historian",
        archetype="Keeper of Time",
        color="mystic-purple",
        icon="history_edu",
        model_role="HISTORIAN",
        temperature=0.35,
        max_tokens=3072,
        knowledge=("wiki", "sessions", "okf"),
        system_prompt=HISTORIAN_PROMPT,
        voice_rules=(
            "Checks whether the question was already asked, and says what was concluded.",
            "Flags when a settled question is being reopened.",
            "Historical precedent is not proof — says when a question is genuinely new.",
        ),
        response_model=CharacterStatement,
        stances=("propose", "support", "challenge", "question"),
        description=(
            "The only character who can say 'we already answered this, and we were "
            "wrong' — reading the prior record and the wiki log."
        ),
        tags=("Temporal", "Precedent-Aware", "Continuity"),
    ),
)

BY_KEY: dict[str, Character] = {c.key: c for c in _CHARACTERS}

#: Canonical speaking order. The Moderator invites from this; the thread
#: renders in it so the same character is always in the same place.
ORDER: tuple[str, ...] = ("oracle", "scholar", "analyst", "historian", "sage")

#: Backwards-compatible alias for the chamber modules, which still use the
#: legacy agent keys. Derived from the registry rather than being a second
#: source of truth.
COUNCIL_AGENTS: tuple[str, ...] = ("oracle", "rag", "sage")


def all_characters() -> list[Character]:
    return list(_CHARACTERS)


def keys() -> list[str]:
    """Every character key, in canonical speaking order."""
    return [k for k in ORDER if k in BY_KEY] + [k for k in BY_KEY if k not in ORDER]


#: Legacy agent keys mapped onto character keys. The chamber graph, the
#: critique round and the Archive all still say "rag"; the character is
#: "scholar". Kept here so the mapping lives in exactly one place.
LEGACY_KEYS: dict[str, str] = {
    "rag": "scholar",
    "scout": "scholar",
    "historian_v1": "historian",
}


def resolve_key(key: str) -> Optional[str]:
    """Map a legacy agent key onto a character key, if one applies."""
    k = (key or "").strip().lower()
    return LEGACY_KEYS.get(k, k)


def get(key: str) -> Optional[Character]:
    k = resolve_key(key)
    return BY_KEY.get(k) if k else None


def require(key: str) -> Character:
    c = get(key)
    if c is None:
        raise KeyError(f"Unknown character {key!r}. Known: {', '.join(keys())}")
    return c


def display_name(key: str) -> str:
    c = get(key)
    return c.display_name if c else key


def ordered(cast: list[str]) -> list[str]:
    """Sort a cast into canonical speaking order, dropping unknown keys.

    Legacy keys are resolved first, so a cast stored by an older version
    (`["oracle", "rag"]`) still produces the right speakers instead of
    silently becoming empty.
    """
    wanted = set()
    for k in cast:
        if not k:
            continue
        resolved = resolve_key(k)
        if resolved:
            wanted.add(resolved)
    return [k for k in keys() if k in wanted]


def validate_cast(cast: list[str]) -> list[str]:
    """Validate an explicit cast.

    Raises on an unknown character rather than silently dropping it — a typo
    in a moderator's cast should be visible, not quietly shrink the Council.
    Legacy keys are accepted and mapped.
    """
    unknown = [k for k in cast if get(k) is None]
    if unknown:
        raise KeyError(f"Unknown character(s): {', '.join(unknown)}")
    if not cast:
        raise ValueError("A topic needs at least one character")
    if len(cast) > 5:
        raise ValueError("A topic can invite at most five characters")
    return ordered(cast)


def default_cast() -> list[str]:
    return list(ORDER)


def briefs() -> list[dict]:
    """Every character's public card."""
    return [BY_KEY[k].brief() for k in keys()]