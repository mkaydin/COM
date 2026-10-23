"""Running one character.

Each character reads only its own knowledge sources, speaks with its own model
at its own temperature, and returns a `CharacterStatement`. That triple —
sources, model, voice — is what makes five characters worth reading instead
of one voice in five fonts.

Retrieval is best-effort: a character whose knowledge system is down still
speaks, and says `insufficient_data`. A forum where the Scholar silently
disappears because Postgres was slow is worse than one where the Scholar
admits it has nothing.
"""

import asyncio
from typing import Optional

from llm import llm_complete
from agents import registry
from agents.json_utils import extract_json
from logging_config import get_logger

logger = get_logger(__name__)

MAX_TOKENS_CAP = 4096


async def _okf_context(query: str, top_k: int = 5) -> str:
    from okf.bundle import format_context, retrieve_relevant

    try:
        concepts = await asyncio.to_thread(retrieve_relevant, query, None, top_k)
        if not concepts:
            return ""
        return await asyncio.to_thread(format_context, concepts)
    except Exception as e:
        logger.warning("forum_okf_retrieval_failed", error=str(e)[:120])
        return ""


async def _rag_context(query: str, top_k: int = 8) -> str:
    try:
        from db.postgres import async_session
        from rag.retrieval import retrieve

        async with async_session() as db:
            result = await retrieve(db, query, top_k=top_k)
        return (result or {}).get("context", "")
    except Exception as e:
        logger.warning("forum_rag_retrieval_failed", error=str(e)[:120])
        return ""


async def _wiki_context(query: str, max_pages: int = 4) -> str:
    try:
        from agents.sage_wiki import load_index, search_wiki

        pages = search_wiki(query, max_pages=max_pages)
        blocks = []
        for p in pages:
            blocks.append(f"[[{p['path']}]] {p['title']}\n{p['content'][:1200]}")
        idx = load_index()[:1200]
        return ("WIKI INDEX:\n" + idx + "\n\n" + "\n---\n".join(blocks)).strip()
    except Exception as e:
        logger.warning("forum_wiki_retrieval_failed", error=str(e)[:120])
        return ""


async def _graph_context(query: str) -> str:
    """Neo4j traversal — optional, since it runs behind the `graph` profile."""
    # Token-overlap entity lookup; the graph is usually sparse early on,
    # so an empty result is the normal case and not an error.
    words = [w for w in query.lower().split() if len(w) > 4][:5]
    if not words:
        return ""
    try:
        from db.neo4j import get_driver

        driver = get_driver()
        if driver is None:
            return ""
        lines = await asyncio.to_thread(_graph_lookup, driver, words)
        return "GRAPH ENTITIES:\n" + "\n".join(lines) if lines else ""
    except Exception as e:
        # `debug` used to make a stalled Neo4j invisible; this is an optional
        # source on the Analyst's critical path, so a failure is worth seeing.
        logger.warning("forum_graph_retrieval_unavailable", error=str(e)[:120])
        return ""


def _graph_lookup(driver, words: list[str]) -> list[str]:
    """The blocking Neo4j round-trip. Always called via `asyncio.to_thread`.

    The sync driver blocks for its full timeout if the database is unreachable.
    Called directly from the event loop that stalls every other request in the
    process, which is why the offload is not optional.
    """
    with driver.session() as session:
        result = session.run(
            "MATCH (n) WHERE any(w IN $words WHERE toLower(n.name) CONTAINS w) "
            "RETURN n.name AS name, labels(n) AS labels LIMIT 10",
            words=words,
        )
        rows = [r for r in result]
    return [f"- {r['name']} ({', '.join(r['labels'])})" for r in rows]


async def _session_context(session_id: Optional[str]) -> str:
    """Prior deliberations — the Historian's raw material."""
    if not session_id:
        return ""
    try:
        from agents.session_memory import load_session_memory

        return await load_session_memory(str(session_id))
    except Exception as e:
        logger.warning("forum_session_memory_failed", error=str(e)[:120])
        return ""


async def gather_context(
    character: registry.Character, query: str, session_id: Optional[str] = None
) -> str:
    """Everything this character is allowed to read, assembled."""
    parts: list[str] = []

    # Sources are read concurrently where they are independent I/O.
    tasks = []
    for source in character.knowledge:
        if source == "okf":
            tasks.append(_okf_context(query))
        elif source in ("rag", "documents"):
            tasks.append(_rag_context(query))
        elif source == "wiki":
            tasks.append(_wiki_context(query))
        elif source == "graph":
            tasks.append(_graph_context(query))
        elif source == "sessions":
            tasks.append(_session_context(session_id))
        else:
            # Fail loudly. This used to fall through silently, so a typo in a
            # character's knowledge tuple left it reading nothing from that
            # source while its prompt still promised the context.
            logger.warning(
                "forum_unknown_knowledge_source", character=character.key, source=source
            )

    for coro_result in await asyncio.gather(*tasks) if tasks else []:
        if coro_result and coro_result.strip():
            parts.append(coro_result.strip())

    return "\n\n".join(parts)


def _render_thread(posts: list[dict], limit: int = 12) -> str:
    """The visible thread so far, as text.

    Truncated by recency rather than by character: a character needs to see
    what was said *last*, not a full transcript, and free-tier context is not
    free.
    """
    lines = []
    for p in posts[-limit:]:
        marker = ""
        if p.get("reply_to_seq"):
            marker = f" (replying to #{p['reply_to_seq']})"
        body = (p.get("body") or "").strip()
        if len(body) > 900:
            body = body[:900] + " …"
        lines.append(
            f"#{p['seq']} {p['author'].upper()} [{p.get('stance', '')}]{marker}\n{body}"
        )
    return "\n\n".join(lines)


async def run_character(
    key: str,
    query: str,
    *,
    posts: Optional[list[dict]] = None,
    session_id: Optional[str] = None,
    mode: str = "exchange",
) -> dict:
    """Produce one character's contribution for a stage.

    `mode` is one of:

    * ``statement`` — the opening pass: state a position, nobody has spoken yet.
    * ``exchange`` — a discussion round: reply to a specific claim.
    * ``revision`` — the final pass: restate or change their own answer after
      reading the whole discussion. This is what makes a "final answer" exist;
      without it a character's last word was whatever they last replied to.

    Returns a dict shaped like a post body plus the structured fields the
    convergence check reads (`claims`, `confidence`, `stance`).
    """
    character = registry.require(key)
    posts = posts or []
    mine = [p for p in posts if p.get("author") == key]

    context = await gather_context(character, query, session_id)

    if mode == "statement":
        instruction = (
            "This is the opening of a new topic. State your position on the question "
            "in your own voice, following your rules. Set stance to \"propose\"."
        )
    elif mode == "revision":
        # The revision sees the whole discussion, not a truncated window: the
        # point of the pass is to answer everything that was raised.
        thread = _render_thread(posts, limit=40)
        opening = next(
            (p for p in mine if p.get("kind") == "statement"),
            mine[0] if mine else None,
        )
        instruction = (
            "This is the REVISION pass. The discussion is over. Write your FINAL "
            "answer to the topic question.\n\n"
            f"THE DISCUSSION:\n{thread or '(no posts yet)'}\n\n"
            + (
                f"YOUR OPENING ANSWER:\n{(opening.get('body') or '').strip()}\n\n"
                if opening
                else ""
            )
            + "Decide for yourself whether your opening still holds. If the "
            "discussion changed your position, answer differently and say what "
            "changed it. If it did not, restate it more precisely. Write a position "
            "the Moderator can publish as your final answer — do not comment on other "
            "characters in general terms. Set stance to \"propose\"."
        )
    else:
        thread = _render_thread(posts)
        instruction = (
            "The thread so far:\n\n"
            f"{thread or '(no posts yet)'}\n\n"
            "Respond to the specific claim you are answering — not to the topic in "
            "general. Pick the post number you are replying to and put it in `reply_to`.\n"
            + (
                "You have already spoken. Advance the conversation; do not repeat yourself.\n"
                f"Your earlier posts:\n{_render_thread(mine, limit=3)}"
                if mine
                else ""
            )
        )

    messages = [
        {"role": "system", "content": character.system_prompt},
        {
            "role": "user",
            "content": (
                f"TOPIC QUESTION: {query}\n\n"
                + (f"YOUR KNOWLEDGE SOURCES:\n{context}\n\n" if context else "")
                + f"{instruction}"
            ),
        },
    ]

    try:
        response = await llm_complete(
            messages,
            role=character.model_role,
            temperature=character.temperature,
            max_tokens=min(character.max_tokens, MAX_TOKENS_CAP),
        )
        raw = response.content
        data = extract_json(raw)
        if not data:
            raise ValueError("no parseable JSON in character output")
        statement = registry.CharacterStatement(**data)
    except Exception as e:
        logger.error("forum_character_failed", character=key, error=str(e)[:160])
        # A provider-level failure (no API key, unreachable endpoint, rate
        # limit) hits every character in every round. Writing a post per
        # failure would bury the thread in identical apologies, so `ok=False`
        # means the caller skips the post entirely and the condition is
        # surfaced once, in the synthesis.
        return {
            "author": key,
            "stance": "propose",
            "body": "",
            "confidence": 0.0,
            "claims": [],
            "citations": [],
            "extra": {"error": str(e)[:200]},
            "ok": False,
        }

    conf = statement.confidence
    if conf > 1.5:
        conf = conf / 100.0

    stance = (statement.stance or "propose").strip().lower()
    if stance not in character.stances:
        # The model invented a stance this character does not take; fall back
        # to a meaningful one rather than storing something meaningless.
        # Statements and revisions both mean "here is my position"; only an
        # exchange reply falls back to the character's characteristic stance.
        stance = "propose" if mode in ("statement", "revision") else character.stances[0]

    claims = [c.model_dump() for c in statement.claims]
    citations = [c.model_dump() for c in statement.citations]

    return {
        "author": key,
        "stance": stance,
        "body": statement.verdict.strip(),
        "confidence": max(0.0, min(1.0, conf)),
        "claims": claims,
        "citations": citations,
        "reply_to_seq": statement.reply_to or None,
        "extra": {
            "analogies": [a.model_dump() for a in statement.analogies],
            "causal_chains": [c.model_dump() for c in statement.causal_chains],
            "hidden_links": [h.model_dump() for h in statement.hidden_links],
            "ethical_dimensions": statement.ethical_dimensions,
            "prior_conclusions": [p.model_dump() for p in statement.prior_conclusions],
            "uncertainties": statement.uncertainties,
            "gaps": statement.gaps,
            "novelty": statement.novelty,
            "speculation_warning": statement.speculation_warning,
            "insufficient_data": statement.insufficient_data,
        },
        "ok": True,
    }