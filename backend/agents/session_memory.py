"""Session memory — a compact summary of the prior deliberations in a chat session.

Each follow-up query runs as a *new* Deliberation in the same session, so by
default the graph starts with no knowledge of what was already asked/decided.
This module rebuilds that context from the database so the Moderator (and
optionally the agents) can stay coherent across a conversation: it sees earlier
queries, which conflicts were resolved, which are still open, and what decision
options were mapped.

The summary is loaded once per query (in the dispatch layer) and passed into the
graph via state["session_memory"], so the Moderator never hits the DB itself and
the cost is a single query per follow-up.
"""
from uuid import UUID
from sqlalchemy import select

from logging_config import get_logger

logger = get_logger(__name__)


def _summarize_moderator(mod: dict) -> str:
    """One-line digest of a prior deliberation's moderator synthesis."""
    if not isinstance(mod, dict) or not mod:
        return "(no synthesis recorded)"
    facts = []
    alignment = mod.get("alignment")
    if alignment:
        facts.append(f"alignment {alignment}")
    conflicts = mod.get("conflicts") or []
    resolved = [c.get("topic", "") for c in conflicts if c.get("status") == "resolved"]
    open_c = [c.get("topic", "") for c in conflicts if c.get("status") != "resolved"]
    if resolved:
        facts.append("resolved: " + str([t for t in resolved if t]))
    if open_c:
        facts.append("still open: " + str([t for t in open_c if t]))
    decisions = [d.get("label", "") for d in (mod.get("decision_space") or []) if d.get("label")]
    if decisions:
        facts.append("options: " + str(decisions))
    return "; ".join(facts) if facts else "(no structured outcome)"


async def load_session_memory(
    session_id: str,
    exclude_deliberation_id: str | None = None,
    limit: int = 5,
) -> str:
    """Return a short narrative of prior deliberations in `session_id`.

    `exclude_deliberation_id` skips the in-flight deliberation (the one we're
    about to run) so it isn't listed as "prior".
    """
    from db.postgres import async_session
    from db.models import Deliberation, AgentResponse

    try:
        sid = UUID(str(session_id))
    except (TypeError, ValueError):
        logger.warning("session_memory_bad_session_id", session_id=session_id)
        return ""

    try:
        async with async_session() as db:
            stmt = (
                select(Deliberation, AgentResponse.response_json)
                .select_from(Deliberation)
                .outerjoin(
                    AgentResponse,
                    (AgentResponse.deliberation_id == Deliberation.id)
                    & (AgentResponse.agent_name == "moderator"),
                )
                .where(Deliberation.session_id == sid)
                .order_by(Deliberation.created_at.desc())
                .limit(limit)
            )
            if exclude_deliberation_id:
                try:
                    stmt = stmt.where(Deliberation.id != UUID(str(exclude_deliberation_id)))
                except (TypeError, ValueError):
                    pass
            rows = (await db.execute(stmt)).all()
    except Exception as e:
        logger.warning("session_memory_load_failed", session_id=str(session_id), error=str(e))
        return ""

    if not rows:
        return ""

    # chronological (oldest first) reads as a proper narrative
    rows = list(reversed(rows))
    lines = [
        "PRIOR DELIBERATIONS IN THIS SESSION (for continuity — build on earlier "
        "conclusions; do NOT re-open conflicts already marked resolved):"
    ]
    for i, (delib, mod_json) in enumerate(rows, 1):
        query = (delib.user_query or "").strip()
        digest = _summarize_moderator(mod_json)
        lines.append(f'{i}. "{query[:180]}" -> {digest}')
    return "\n".join(lines)
