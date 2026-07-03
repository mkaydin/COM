from typing import Any, Optional

from config import get_settings
from logging_config import get_logger
from agents.schemas import ModeratorResponse, Agreement, Conflict
from agents.prompts import MODERATOR_SYSTEM_PROMPT
from agents.json_utils import schema_for_prompt, extract_json

logger = get_logger(__name__)
settings = get_settings()


# The council that actually runs (see agents/graph.py). Order matters for output.
COUNCIL_AGENTS = ("oracle", "rag", "sage")
AGENT_DISPLAY = {"oracle": "Oracle", "rag": "Scholar (RAG)", "sage": "Sage"}

# Thresholds for the deterministic signals
SPREAD_ALIGN_HIGH = 0.15   # < this (and no insufficient) => high alignment
SPREAD_ALIGN_LOW = 0.40    # > this (or 2+ insufficient) => low alignment
SPREAD_CRITIQUE = 0.45     # confidence spread above this forces a critique round


# --------------------------------------------------------------------------------------
# Content extraction — turn a raw agent response into a clean, content-focused brief
# --------------------------------------------------------------------------------------

def _norm_conf(v: Any) -> Optional[float]:
    """Normalize confidence to a 0..1 float.

    Agents are inconsistent in the codebase: some store 0..1, some 0..100.
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f > 1.5:  # almost certainly a 0..100 value
        f = f / 100.0
    return max(0.0, min(1.0, f))


def _agent_brief(resp: Optional[dict]) -> dict:
    """Extract the meaningful fields of one agent response (no schema noise, no truncation)."""
    if not resp:
        return {"present": False}
    main = (resp.get("verdict") or resp.get("answer") or resp.get("synthesis") or "").strip()
    claims = resp.get("claims") or []
    citations = resp.get("citations") or []
    analogies = resp.get("analogies") or []
    return {
        "present": True,
        "confidence": _norm_conf(resp.get("confidence")),
        "reasoning_type": resp.get("reasoning_type"),
        "main": main,
        "claims": [c.get("text") if isinstance(c, dict) else str(c) for c in claims],
        "citations": [
            (c.get("title") or c.get("source_url") or "")
            for c in citations if isinstance(c, dict)
        ],
        "analogies": [a.get("concept") if isinstance(a, dict) else str(a) for a in analogies],
        "insufficient": bool(resp.get("insufficient_data")),
    }


# --------------------------------------------------------------------------------------
# Deterministic signals — cheap, reliable cross-checks computed from the briefs
# --------------------------------------------------------------------------------------

def _tokens(text: str) -> set:
    """Lowercase content-word token set (len > 3) for overlap comparison."""
    cleaned = "".join(ch if (ch.isalnum() or ch in "-'") else " " for ch in (text or "").lower())
    return {t for t in cleaned.split() if len(t) > 3}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b) or 1
    return inter / union


def _compute_signals(briefs: dict) -> dict:
    """Compute deterministic alignment/conflict hints independent of the LLM."""
    present = {n: b for n, b in briefs.items() if isinstance(b, dict) and b.get("present")}
    confs = [b["confidence"] for b in present.values() if b.get("confidence") is not None]
    spread = (max(confs) - min(confs)) if len(confs) >= 2 else 0.0
    mean_conf = (sum(confs) / len(confs)) if confs else None
    insufficient = [n for n, b in present.items() if b.get("insufficient")]

    # Do Oracle and Scholar reference the same subject matter?
    orc = briefs.get("oracle") or {}
    rg = briefs.get("rag") or {}
    oracle_text = " ".join([orc.get("main", "")] + orc.get("claims", []))
    rag_text = " ".join([rg.get("main", "")] + rg.get("citations", []))
    overlap = _jaccard(_tokens(oracle_text), _tokens(rag_text))

    if spread < SPREAD_ALIGN_HIGH and not insufficient:
        alignment = "high"
    elif spread > SPREAD_ALIGN_LOW or len(insufficient) >= 2:
        alignment = "low"
    else:
        alignment = "medium"

    return {
        "confidence_spread": round(spread, 3),
        "mean_confidence": round(mean_conf, 3) if mean_conf is not None else None,
        "insufficient_agents": insufficient,
        "oracle_rag_overlap": round(overlap, 3),
        "alignment": alignment,
    }


# --------------------------------------------------------------------------------------
# Prompt formatting
# --------------------------------------------------------------------------------------

def _format_briefs(briefs: dict, signals: dict) -> str:
    blocks = []
    for name in COUNCIL_AGENTS:
        b = briefs.get(name) or {}
        disp = AGENT_DISPLAY.get(name, name)
        if not b.get("present"):
            blocks.append(f"=== {disp} ===\n(no response)\n")
            continue
        parts = [f"=== {disp} ==="]
        c = b.get("confidence")
        parts.append(f"confidence: {round(c * 100)}%" if c is not None else "confidence: n/a")
        if b.get("reasoning_type"):
            parts.append(f"reasoning: {b['reasoning_type']}")
        if b.get("insufficient"):
            parts.append("NOTE: this agent reported insufficient data")
        if b.get("main"):
            parts.append(b["main"])
        if b.get("claims"):
            parts.append("key claims: " + "; ".join(b["claims"][:6]))
        if b.get("citations"):
            parts.append("citations: " + "; ".join(x for x in b["citations"][:6] if x))
        blocks.append("\n".join(parts) + "\n")

    mean_pct = f"{round(signals['mean_confidence'] * 100)}%" if signals["mean_confidence"] is not None else "n/a"
    hints = (
        "DETERMINISTIC SIGNALS (computed for you — rely on these, verify against the text above):\n"
        f"- council alignment: {signals['alignment']}\n"
        f"- confidence spread: {round(signals['confidence_spread'] * 100)}% (mean {mean_pct})\n"
        f"- agents reporting insufficient data: {signals['insufficient_agents'] or 'none'}\n"
        f"- oracle/scholar source overlap: {round(signals['oracle_rag_overlap'] * 100)}%"
    )
    return "\n".join(blocks) + "\n" + hints


# --------------------------------------------------------------------------------------
# Fallback — when the LLM call fails, still produce a useful (non-empty) synthesis
# --------------------------------------------------------------------------------------
# Critique-outcome reconciliation (used when re-synthesizing after a critique round)
# --------------------------------------------------------------------------------------

def _norm_topic(t: str) -> str:
    return "".join(ch for ch in (t or "").lower() if ch.isalnum())


def _topics_match(a: str, b: str) -> bool:
    """Fuzzy topic match so a moderator conflict and a critique outcome line up
    even when the LLM phrases them slightly differently."""
    na, nb = _norm_topic(a), _norm_topic(b)
    if not na or not nb:
        return False
    if len(na) < 5 or len(nb) < 5:
        return na == nb
    return na in nb or nb in na


def _format_critique_outcomes(critiques: list) -> str:
    """Render accumulated critique-round results as hints for the moderator."""
    if not critiques:
        return ""
    lines = ["PRIOR CRITIQUE OUTCOMES (from the debate round(s)) — reflect these in your synthesis:"]
    for c in critiques:
        topic = c.get("topic", "unknown")
        status = "RESOLVED" if c.get("resolved") else "STILL UNRESOLVED"
        members = c.get("critiques") or []
        if members:
            parts = []
            for m in members:
                who = m.get("from") or m.get("agent") or "?"
                stance = m.get("stance", "hold")
                rev = m.get("revised")
                bit = f"{who} {stance}"
                if stance in ("revise", "concede") and rev:
                    bit += f" -> {rev}"
                parts.append(bit)
            detail = "; ".join(parts)
        elif c.get("note"):
            detail = c.get("note")
        else:
            detail = "(no responses)"
        lines.append(f'- "{topic}": {status} — {detail}')
    return "\n".join(lines)


def _reconcile_with_critiques(conflicts, critiques) -> None:
    """Mark a conflict resolved if a matching critique topic was resolved."""
    resolved_topics = [c.get("topic", "") for c in (critiques or []) if c.get("resolved")]
    for conf in conflicts:
        if conf.status == "resolved":
            continue
        for rt in resolved_topics:
            if _topics_match(conf.topic, rt):
                conf.status = "resolved"
                break


# --------------------------------------------------------------------------------------

def _fallback_synthesis(briefs: dict, signals: dict, deliberation_id: str) -> ModeratorResponse:
    present_names = [n for n in COUNCIL_AGENTS if (briefs.get(n) or {}).get("present")]

    agreements = []
    conflicts = []

    if signals["alignment"] == "high" and len(present_names) >= 2:
        agreements.append(Agreement(
            topic="Council converges on the same conclusion (high alignment, low confidence spread).",
            agents=present_names,
        ))

    if signals["confidence_spread"] > SPREAD_ALIGN_LOW:
        ranked = sorted(
            ((n, (briefs.get(n) or {}).get("confidence")) for n in present_names),
            key=lambda x: (x[1] if x[1] is not None else -1),
        )
        if len(ranked) >= 2:
            hi, lo = ranked[-1], ranked[0]
            conflicts.append(Conflict(
                topic="Confidence divergence between agents.",
                severity="nuance",
                status="unresolved",
                **{hi[0]: f"high confidence (~{round((hi[1] or 0) * 100)}%)"},
                **{lo[0]: f"low confidence (~{round((lo[1] or 0) * 100)}%)"},
            ))

    for agent_name in signals["insufficient_agents"]:
        conflicts.append(Conflict(
            topic=f"{AGENT_DISPLAY.get(agent_name, agent_name)} reported insufficient data.",
            severity="nuance",
            status="unresolved",
        ))

    return ModeratorResponse(
        deliberation_id=deliberation_id,
        comparison={"fallback": True, "signals": signals},
        conflicts=conflicts,
        agreements=agreements,
        decision_space=[],
        alignment=signals["alignment"],
    )


# --------------------------------------------------------------------------------------
# Node
# --------------------------------------------------------------------------------------

async def moderator_node(state: dict) -> dict:
    logger.info("moderator_synthesizing")
    deliberation_id = state.get("deliberation_id", "")
    provider = state.get("provider", "lm_studio")

    critique_round = state.get("critique_round", 0)
    max_rounds = state.get("max_critique_rounds", 2)
    prior_critiques = state.get("critiques") or []
    post_critique = critique_round > 0 and bool(prior_critiques)

    briefs = {name: _agent_brief(state.get(f"{name}_response")) for name in COUNCIL_AGENTS}
    signals = _compute_signals(briefs)

    session_memory = state.get("session_memory") or ""

    if provider == "lm_studio":
        from agents.prompts import LOCAL_MODERATOR_PROMPT
        system_content = LOCAL_MODERATOR_PROMPT
        max_tok = 4096
    else:
        system_content = MODERATOR_SYSTEM_PROMPT + schema_for_prompt(ModeratorResponse)
        max_tok = 3072

    user_content = (
        f"USER QUERY: {state.get('user_query', '')}\n\n"
        f"Compare these council responses.\n\n{_format_briefs(briefs, signals)}"
    )
    if session_memory:
        user_content += (
            f"\n\n{session_memory}\n\n"
            "Treat earlier resolved conflicts as settled and maintain coherence "
            "with prior conclusions — unless this query genuinely changes the picture."
        )
    if post_critique:
        user_content += (
            f"\n\n{_format_critique_outcomes(prior_critiques)}\n\n"
            "These conflicts were debated. Mark any that the debate settled as "
            'status="resolved", reflect concessions/revisions in your synthesis, '
            "and raise alignment if the council converged."
        )

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]

    try:
        from config import llm_complete
        response = await llm_complete(messages, temperature=0.2, max_tokens=max_tok, provider=provider)
        raw = response.choices[0].message.content
        data = extract_json(raw)
        moderator_output = ModeratorResponse(**data)
    except Exception as e:
        logger.error("moderator_llm_failed", error=str(e))
        moderator_output = _fallback_synthesis(briefs, signals, deliberation_id)

    # Don't trust the LLM to mark things resolved — reconcile deterministically
    # against the actual critique outcomes.
    if post_critique:
        _reconcile_with_critiques(moderator_output.conflicts, prior_critiques)

    # Deterministic alignment (more trustworthy than self-report).
    moderator_output.alignment = signals["alignment"]
    if post_critique and not [c for c in moderator_output.conflicts if c.status != "resolved"]:
        moderator_output.alignment = "high"  # the debate converged the council

    moderator_output.comparison = {
        **(moderator_output.comparison or {}),
        "signals": signals,
        "post_critique": post_critique,
        "critique_round": critique_round,
        "confidence": {n: (briefs.get(n) or {}).get("confidence") for n in COUNCIL_AGENTS},
    }

    conflicts = moderator_output.conflicts
    unresolved = [c for c in conflicts if c.status != "resolved"]
    material = any(c.severity == "factual" for c in unresolved) or signals["confidence_spread"] > SPREAD_CRITIQUE
    # Only request another critique if material disagreement remains AND we haven't
    # exhausted the round budget (this is what bounds the moderator<->critique loop).
    should_critique = material and len(unresolved) > 0 and critique_round < max_rounds

    out = moderator_output.model_dump()
    logger.info(
        "moderator_complete",
        conflicts=len(conflicts),
        unresolved=len(unresolved),
        factual=sum(1 for c in conflicts if c.severity == "factual"),
        alignment=moderator_output.alignment,
        post_critique=post_critique,
        critique_round=critique_round,
        should_critique=should_critique,
    )

    return {
        "moderator_synthesis": out,
        "should_critique": should_critique,
        "unresolved_conflicts": [c for c in out.get("conflicts", []) if c.get("status") != "resolved"],
        "post_critique": post_critique,
    }
