"""Critique round — when the Moderator flags material, unresolved conflicts,
each involved agent re-examines its position in light of the others' arguments
and either holds, revises, or concedes. Conflicts that converge are marked
resolved; genuinely deadlocked ones stay unresolved (and may trigger one more
bounded round).

This replaces the previous stub, which emitted hardcoded strings and cleared
all conflicts (so the loop was a no-op).
"""
import asyncio
from typing import Any, Optional

from config import llm_complete  # shim -> llm.llm_complete
from logging_config import get_logger
from agents.json_utils import extract_json

logger = get_logger(__name__)

COUNCIL_AGENTS = ("oracle", "rag", "sage")
AGENT_DISPLAY = {"oracle": "Oracle", "rag": "Scholar (RAG)", "sage": "Sage"}

# Legacy agent keys mapped onto character-registry keys. "rag" predates the
# rename to "scholar".
_REGISTRY_KEY = {"rag": "scholar"}


def _model_role_for(agent: str) -> str:
    """The model role an agent critiques on, from the character registry."""
    from agents import registry

    character = registry.get(_REGISTRY_KEY.get(agent, agent))
    return character.model_role if character else "MODERATOR"

# Safety caps so a noisy moderator can't explode LLM cost/latency
MAX_CONFLICTS_PER_ROUND = 4
MAX_TOK = 768


def _agent_position(conflict: dict, agent: str) -> str:
    """An agent's stated position on a conflict (new fields, with legacy fallback)."""
    val = conflict.get(agent)
    if val is None and agent == "oracle":
        val = conflict.get("scout") or conflict.get("historian")
    if val is None and agent == "rag":
        val = conflict.get("detective")
    return val or ""


def _participants(conflict: dict) -> list:
    return [a for a in COUNCIL_AGENTS if _agent_position(conflict, a)]


def _agent_main(agent: str, state: dict) -> str:
    resp = state.get(f"{agent}_response") or {}
    return (resp.get("verdict") or resp.get("answer") or resp.get("synthesis") or "").strip()


CRITIQUE_PROMPT = """You are {agent_name}, a member of the Council of Minds, in a CRITIQUE round.

Original user query: {query}

The Moderator flagged this conflict:
- Topic: {topic}
- Severity: {severity}

Your stated position: {your_position}
Other members' positions:
{opponent_positions}

Your ORIGINAL response (for reference):
{your_original}

Re-evaluate your position given the other members' arguments and the evidence. Be intellectually honest — concede if their evidence is stronger.

Respond with ONLY this JSON:
{{
  "stance": "hold | revise | concede",
  "reasoning": "2-4 sentences citing your evidence",
  "revised_position": "your position if you revise/concede, otherwise restate your original"
}}
- hold: you still disagree and have evidence.
- revise: you partially adjust toward agreement.
- concede: the other members' evidence is stronger; you withdraw your claim.
Do not invent evidence.
"""


async def _critique_as_agent(agent: str, conflict: dict, state: dict) -> dict:
    opponents = [a for a in _participants(conflict) if a != agent]
    opp_text = "\n".join(f"- {AGENT_DISPLAY[o]}: {_agent_position(conflict, o)}" for o in opponents) or "- (none stated)"
    prompt = CRITIQUE_PROMPT.format(
        agent_name=AGENT_DISPLAY[agent],
        query=state.get("user_query", ""),
        topic=conflict.get("topic", ""),
        severity=conflict.get("severity", "nuance"),
        your_position=_agent_position(conflict, agent) or "(no explicit position)",
        opponent_positions=opp_text,
        your_original=_agent_main(agent, state)[:1500],
    )
    try:
        # Each agent re-evaluates in its own voice, so it critiques on its own
        # model. "rag" is the legacy key for the Scholar.
        resp = await llm_complete(
            [{"role": "user", "content": prompt}],
            role=_model_role_for(agent),
            temperature=0.3, max_tokens=MAX_TOK,
        )
        data = extract_json(resp.content)
        stance = str(data.get("stance", "hold")).lower().strip()
        if stance not in ("hold", "revise", "concede"):
            stance = "hold"
        return {
            "agent": agent,
            "stance": stance,
            "reasoning": str(data.get("reasoning", "")).strip(),
            "revised": str(data.get("revised_position", "")).strip(),
            "ok": True,
        }
    except Exception as e:
        logger.warning("critique_call_failed", agent=agent, error=str(e))
        return {"agent": agent, "stance": "hold", "reasoning": "(critique unavailable)", "revised": "", "ok": False}


def _is_resolved(stances: list) -> bool:
    """A conflict converges if someone concedes, or all participating agents revise."""
    if not stances:
        return False
    if any(s == "concede" for s in stances):
        return True
    return len(stances) >= 2 and all(s == "revise" for s in stances)


async def critique_round_node(state: dict) -> dict:
    round_num = state.get("critique_round", 0) + 1
    unresolved = state.get("unresolved_conflicts", []) or []
    max_rounds = state.get("max_critique_rounds", 2)

    logger.info("critique_round_start", round=round_num, conflicts=len(unresolved))

    if not unresolved:
        return {"critiques": [], "critique_round": round_num, "unresolved_conflicts": [], "should_critique": False}

    # Only critique genuine multi-party disagreements; cap for safety.
    critiquable = [c for c in unresolved if len(_participants(c)) >= 2][:MAX_CONFLICTS_PER_ROUND]

    # Build one LLM task per (conflict, participant) and run them concurrently.
    tasks, meta = [], []
    for ci, conflict in enumerate(critiquable):
        for agent in _participants(conflict):
            tasks.append(_critique_as_agent(agent, conflict, state))
            meta.append(ci)

    results = await asyncio.gather(*tasks) if tasks else []
    by_conflict = [[] for _ in critiquable]
    for ci, r in zip(meta, results):
        if r:
            by_conflict[ci].append(r)

    any_ok = False
    critiques_out = []
    still_unresolved = []

    for ci, conflict in enumerate(critiquable):
        res = by_conflict[ci]
        any_ok = any_ok or any(r.get("ok") for r in res)
        stances = [r["stance"] for r in res]
        member_texts = [
            {
                "agent": r["agent"],
                "from": AGENT_DISPLAY.get(r["agent"], r["agent"]),
                "stance": r["stance"],
                "text": r["reasoning"],
                "revised": r.get("revised", ""),
            }
            for r in res
        ]
        resolved = _is_resolved(stances)
        critiques_out.append({
            "round": round_num,
            "topic": conflict.get("topic", "unknown"),
            "severity": conflict.get("severity", "nuance"),
            "resolved": resolved,
            "critiques": member_texts,
        })
        if not resolved:
            still_unresolved.append(conflict)

    # Conflicts we skipped (fewer than 2 participants, e.g. insufficient-data notes)
    # have nothing to debate — report them as resolved so they don't loop forever.
    skipped = [c for c in unresolved if len(_participants(c)) < 2]
    for c in skipped:
        critiques_out.append({
            "round": round_num,
            "topic": c.get("topic", "unknown"),
            "severity": c.get("severity", "nuance"),
            "resolved": True,
            "critiques": [],
            "note": "no opposing positions to reconcile",
        })

    # Don't burn another round if the LLM never responded or nothing's left.
    should_continue = bool(still_unresolved) and round_num < max_rounds and any_ok

    logger.info(
        "critique_round_complete",
        round=round_num,
        critiqued=len(critiquable),
        resolved=len(critiquable) - len(still_unresolved),
        still_unresolved=len(still_unresolved),
        will_continue=should_continue,
    )

    return {
        "critiques": critiques_out,
        "critique_round": round_num,
        "unresolved_conflicts": still_unresolved,
        "should_critique": should_continue,
    }
