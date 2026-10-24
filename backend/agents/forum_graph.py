"""The forum graph.

    open_topic → statements → [exchange ⇄ moderator_synthesis] → END

Where it differs from the old `agents/graph.py`:

* **Ordered, not parallel.** `exchange` runs the cast sequentially so each
  character can read what the others said *this round*. The old graph fanned
  out and only ever compared one-line positions afterwards.
* **Posts, not responses.** Each node writes a `Post` with its own stance and
  reply target, so the thread is legible as a conversation.
* **Two real outcomes.** Consensus (one answer, signed by the characters that
  stand behind it) or plurality (2-4 distinct attributed positions). The old
  graph only ever produced a "decision space" of options and left the verdict
  to the user.
* **Bounded.** `max_rounds` is enforced by `decide_continue`, and a forced
  synthesis always runs at the end, so the graph terminates even if every
  character disagrees forever.

The old chamber graph is untouched and still serves `POST /sessions/{id}/deliberate`.
"""

from typing import Optional, List, Annotated
from typing_extensions import TypedDict
import operator

from agents import registry, forum_signals
from agents.forum_store import (
    append_post,
    create_topic,
    final_answers,
    list_posts,
    bump_round,
    conclude_topic,
)
from agents.forum_characters import run_character
from agents.json_utils import extract_json
from llm import llm_complete
from logging_config import get_logger

logger = get_logger(__name__)

MODERATOR_ROLE = "MODERATOR"
MAX_TITLE = 120


class ForumState(TypedDict, total=False):
    topic_id: str
    question: str
    session_id: Optional[str]
    origin: str

    title: str
    framing: str
    cast: List[str]
    mode: str
    tags: List[str]

    round: int
    max_rounds: int
    round_count: int

    posts: Annotated[List[dict], operator.add]
    #: Characters that could not speak this round. The moderator is told, so
    #: a provider outage is reported rather than synthesised around.
    failed_characters: List[str]
    signals: dict
    outcome: Optional[str]
    synthesis: dict
    converged: bool
    #: Set when a round found nobody with an open challenge. Another round
    #: would invite the same cast to repeat itself.
    no_speakers: bool
    should_continue: bool
    forced: bool
    status: str


# ======================================================================================
# Node: the Moderator opens the topic
# ======================================================================================


def _fallback_opening(question: str) -> dict:
    """A usable topic when the Moderator cannot be reached.

    The whole cast is invited and the mode defaults to `diverse`, because
    guessing "consensus" on a question nobody framed would be worse than
    letting the characters argue it out.
    """
    return {
        "title": question[:MAX_TITLE],
        "framing": question,
        "cast": registry.default_cast(),
        "mode": "diverse",
        "reasoning": "opening fell back to defaults",
    }


async def open_topic(state: ForumState) -> dict:
    """The Moderator titles the topic, frames the question, and picks the cast."""
    question = state.get("question", "")
    requested_cast = state.get("cast") or []
    requested_mode = state.get("mode", "auto")

    messages = [
        {"role": "system", "content": registry.MODERATOR_PROMPT},
        {
            "role": "user",
            "content": (
                "OPEN A NEW TOPIC.\n\n"
                f"RAW QUESTION: {question}\n\n"
                + (
                    f"The user requested this cast: {', '.join(requested_cast)}\n"
                    if requested_cast
                    else "Choose the cast yourself.\n"
                )
            ),
        },
    ]

    data = {}
    try:
        response = await llm_complete(
            messages, role=MODERATOR_ROLE, temperature=0.3, max_tokens=1024
        )
        data = extract_json(response.content)
    except Exception as e:
        logger.error("forum_open_moderator_failed", error=str(e)[:160])

    opening = _fallback_opening(question)
    for field in ("title", "framing", "mode", "reasoning"):
        if data.get(field):
            opening[field] = data[field]

    # Cast: the moderator's pick, validated against the registry. An unknown
    # character falls back to the default cast rather than 500-ing the topic.
    try:
        cast = registry.validate_cast(data.get("cast") or requested_cast or registry.default_cast())
    except (KeyError, ValueError) as e:
        logger.warning("forum_cast_invalid", error=str(e), requested=data.get("cast"))
        cast = registry.validate_cast(requested_cast or registry.default_cast())

    mode = (data.get("mode") or "diverse").strip().lower()
    if mode not in ("consensus", "diverse", "auto"):
        mode = "diverse"
    if requested_mode in ("consensus", "diverse"):
        # An explicit user choice wins over the moderator's guess.
        mode = requested_mode

    from config import get_settings

    max_rounds = state.get("max_rounds") or get_settings().forum_max_rounds

    from db.postgres import async_session
    from uuid import UUID

    async with async_session() as db:
        existing_id = state.get("topic_id")
        if existing_id:
            # The caller already created the topic row so clients can subscribe
            # before the run starts. Fill it in rather than creating a second,
            # orphan one.
            topic = await create_topic(
                db,
                question,
                title=(opening["title"] or question)[:MAX_TITLE],
                framing=opening.get("framing"),
                origin=state.get("origin", "user"),
                mode=mode,
                cast=cast,
                tags=state.get("tags") or [],
                session_id=state.get("session_id"),
                max_rounds=max_rounds,
                topic_id=UUID(existing_id),
            )
        else:
            topic = await create_topic(
                db,
                question,
                title=(opening["title"] or question)[:MAX_TITLE],
                framing=opening.get("framing"),
                origin=state.get("origin", "user"),
                mode=mode,
                cast=cast,
                tags=state.get("tags") or [],
                session_id=state.get("session_id"),
                max_rounds=max_rounds,
            )

        # The opening post is the Moderator's framing — it anchors the thread.
        # Returned in the node output so a streaming caller can render it
        # immediately instead of waiting for the first character to speak.
        opening_post = await append_post(
            db,
            UUID(topic["id"]),
            "moderator",
            opening.get("framing") or question,
            author_kind="moderator",
            stance="open",
            round=0,
            kind="opening",
            extra={"reasoning": opening.get("reasoning"), "mode": mode},
            commit=False,
        )
        await db.commit()

    logger.info(
        "forum_topic_opened",
        topic_id=topic["id"],
        cast=cast,
        mode=mode,
        title=topic["title"],
    )
    return {
        "topic_id": topic["id"],
        "title": topic["title"],
        "framing": opening.get("framing"),
        "cast": cast,
        "mode": mode,
        "max_rounds": max_rounds,
        "round": 0,
        "posts": [opening_post],
    }


# ======================================================================================
# Node: opening statements
# ======================================================================================


async def statements(state: ForumState) -> dict:
    """Every invited character states their position. Fan-out is fine here —
    nobody is responding to anyone yet."""
    import asyncio

    from db.postgres import async_session
    from uuid import UUID

    question = state.get("question", "")
    topic_id = state.get("topic_id")
    cast = state.get("cast") or []

    existing = []
    async with async_session() as db:
        existing = await list_posts(db, UUID(topic_id))

    results = await asyncio.gather(
        *(run_character(k, question, posts=existing, session_id=state.get("session_id"),
                        mode="statement") for k in cast),
        return_exceptions=True,
    )

    new_posts = []
    failed = []
    async with async_session() as db:
        for key, result in zip(cast, results):
            if isinstance(result, Exception) or not result:
                logger.error("forum_statement_failed", character=key,
                             error=str(result)[:160] if result else "none")
                failed.append(key)
                continue
            if not result.get("ok"):
                # Provider-level failure — see run_character's docstring.
                logger.warning("forum_statement_skipped", character=key,
                               error=(result.get("extra") or {}).get("error", "")[:160])
                failed.append(key)
                continue
            post = await append_post(
                db,
                UUID(topic_id),
                key,
                result["body"],
                author_kind="character",
                stance="propose",
                round=0,
                kind="statement",
                confidence=result.get("confidence"),
                claims=result.get("claims"),
                citations=result.get("citations"),
                extra=result.get("extra"),
                commit=False,
            )
            new_posts.append(post)
        await db.commit()

    logger.info("forum_statements_complete", topic_id=topic_id,
                posted=len(new_posts), failed=failed)
    return {"posts": new_posts, "failed_characters": failed}


# ======================================================================================
# Node: the exchange — characters talk to each other
# ======================================================================================


async def exchange(state: ForumState) -> dict:
    """One round of replies, cast in canonical speaking order.

    Sequential on purpose. Each character must read what the ones before them
    said *in this round*, which is the whole difference between a forum and a
    fan-out. It is also what makes `seq` safe — `append_post` allocates it
    under a row lock regardless, but we are not relying on that.
    """
    from db.postgres import async_session
    from uuid import UUID

    question = state.get("question", "")
    topic_id = state.get("topic_id")
    cast = state.get("cast") or []
    round_no = (state.get("round") or 0) + 1

    async with async_session() as db:
        existing = await list_posts(db, UUID(topic_id))

    # Everyone speaks in the first round. After that, only characters whose
    # claim is still in dispute — a second full round of the same cast is the
    # most expensive way to produce restatements. `decide_continue` still owns
    # the round bound; this only decides who is worth inviting.
    speakers = cast
    if round_no > 1:
        speakers = forum_signals.next_round_speakers(existing, cast)
        if not speakers:
            logger.info(
                "forum_exchange_skipped",
                topic_id=topic_id, round=round_no,
                reason="no unresolved challenges",
            )
            return {
                "posts": [], "round": state.get("round", 0),
                "failed_characters": [], "no_speakers": True,
            }

    new_posts = []
    failed = []
    async with async_session() as db:
        # Re-read inside the transaction: each iteration needs the posts that
        # the previous characters just wrote in this same round.
        async def _loop():
            nonlocal new_posts, failed
            for key in speakers:
                so_far = await list_posts(db, UUID(topic_id))
                result = await run_character(
                    key, question, posts=so_far, session_id=state.get("session_id"),
                    mode="exchange",
                )
                if not result or not result.get("ok"):
                    failed.append(key)
                    continue
                reply_to_seq = result.get("reply_to_seq")
                reply_to_id = None
                if reply_to_seq is not None:
                    match = next((p for p in so_far if p.get("seq") == reply_to_seq), None)
                    reply_to_id = match["id"] if match else None

                post = await append_post(
                    db,
                    UUID(topic_id),
                    key,
                    result["body"],
                    author_kind="character",
                    stance=result["stance"],
                    round=round_no,
                    kind="reply",
                    reply_to=reply_to_id,
                    reply_to_seq=reply_to_seq,
                    confidence=result.get("confidence"),
                    claims=result.get("claims"),
                    citations=result.get("citations"),
                    extra=result.get("extra"),
                    commit=False,
                )
                new_posts.append(post)

        await _loop()
        await bump_round(db, UUID(topic_id), commit=False)
        await db.commit()

    logger.info("forum_exchange_complete", topic_id=topic_id, round=round_no,
                posted=len(new_posts), failed=failed)
    return {"posts": new_posts, "round": round_no, "failed_characters": failed}


# ======================================================================================
# Node: every character revises its own answer
# ======================================================================================


async def revise(state: ForumState) -> dict:
    """Each character restates its own answer after reading the discussion.

    Parallel, unlike `exchange`. A revision is not a reply, so nobody should be
    able to react to somebody else's revision — sequential revisions would give
    the last speaker an advantage and make "who changed their mind" partly an
    artefact of speaking order.

    This pass is what makes a final answer exist. Without it a character's last
    word was whatever they happened to reply to.
    """
    import asyncio

    from db.postgres import async_session
    from uuid import UUID

    question = state.get("question", "")
    topic_id = state.get("topic_id")
    cast = state.get("cast") or []
    round_no = (state.get("round") or 0) + 1

    async with async_session() as db:
        posts = await list_posts(db, UUID(topic_id))

    openings = {
        p["author"]: p
        for p in posts
        if p.get("author_kind") == "character" and p.get("kind") == "statement"
    }

    results = await asyncio.gather(
        *(
            run_character(
                key, question, posts=posts, session_id=state.get("session_id"),
                mode="revision",
            )
            for key in cast
        ),
        return_exceptions=True,
    )

    new_posts = []
    failed = []
    async with async_session() as db:
        for key, result in zip(cast, results):
            if isinstance(result, Exception) or not result or not result.get("ok"):
                logger.warning(
                    "forum_revision_skipped",
                    character=key,
                    error=(str(result)[:160] if isinstance(result, Exception)
                           else (result or {}).get("extra", {}).get("error", "")[:160]),
                )
                failed.append(key)
                continue

            opening = openings.get(key)
            post = await append_post(
                db,
                UUID(topic_id),
                key,
                result["body"],
                author_kind="character",
                kind="revision",
                stance="propose",
                round=round_no,
                revision_of=opening["id"] if opening else None,
                confidence=result.get("confidence"),
                claims=result.get("claims"),
                citations=result.get("citations"),
                extra=result.get("extra"),
                commit=False,
            )
            new_posts.append(post)

        await bump_round(db, UUID(topic_id), commit=False)
        await db.commit()

    logger.info(
        "forum_revision_complete",
        topic_id=topic_id,
        round=round_no,
        revised=len(new_posts),
        failed=failed,
    )
    return {"posts": new_posts, "round": round_no, "failed_characters": failed}


# ======================================================================================
# Node: the Moderator synthesises
# ======================================================================================


def _fallback_synthesis(question: str, signals: dict) -> dict:
    """A non-empty synthesis when the Moderator cannot be reached.

    Deterministic: the largest agreement cluster becomes the answer when the
    thread converged, otherwise every character becomes its own position. The
    user still gets something readable and honest rather than an error.
    """
    # Nobody spoke at all — say that, rather than implying a considered answer.
    if not signals.get("cast"):
        return {
            "mode": "plurality",
            "answer": "",
            "positions": [],
            "fault_line": (
                "no character produced a statement; the LLM provider is "
                "unreachable or misconfigured"
            ),
            "dissent": [],
            "signals_note": signals.get("reason", "no character posts"),
        }

    if signals.get("converged") and signals.get("largest_cluster"):
        cluster = signals["largest_cluster"]
        return {
            "mode": "consensus",
            "answer": (
                f"The Council converged on the following, with {', '.join(cluster)} "
                f"holding the same position on: {question}"
            ),
            "positions": [],
            "fault_line": "",
            "dissent": [],
            "signals_note": "synthesised from deterministic signals (moderator unavailable)",
        }

    cast = signals.get("cast") or []
    return {
        "mode": "plurality",
        "answer": "",
        "positions": [
            {
                "label": registry.display_name(k),
                "position": "(this character's position is in the thread above)",
                "backers": [k],
            }
            for k in cast
        ],
        "fault_line": (
            "the characters did not converge; the moderator was unavailable to "
            "synthesise their positions"
        ),
        "dissent": [],
        "signals_note": f"deterministic signals: {signals.get('reason', 'n/a')}",
    }


def _thread_for_moderator(posts: list[dict]) -> str:
    """The character posts as the Moderator sees them, truncated by recency."""
    text = "\n\n".join(
        f"#{p['seq']} {p['author'].upper()} [{p['stance']}]\n{(p['body'] or '').strip()}"
        for p in posts
        if p["author_kind"] == "character"
    )
    # Context is not free: the tail is what the discussion ended on.
    return text[-24000:]


def _finals_for_moderator(finals: list[dict]) -> str:
    """The revision-pass answers, shown separately from the transcript.

    The Moderator is asked to summarise the finals; making it infer them from
    the thread invited it to paraphrase the discussion instead.
    """
    return "\n\n".join(
        f"{f['author'].upper()} (final, #{f['seq']}"
        + (", CHANGED POSITION" if f["changed"] else "")
        + f"):\n{(f['body'] or '').strip()}"
        for f in finals
    ) or "(no character produced a final answer)"


def _outage_note(cast: list[str], signals: dict) -> str:
    """Say it when invited characters could not speak.

    Publishing an empty verdict reads like a considered position; naming the
    outage does not.
    """
    silent = [k for k in cast if k not in signals.get("cast", [])]
    if not silent:
        return ""
    if not signals.get("cast"):
        return (
            f"NO CHARACTER POSTED. {', '.join(silent)} were invited but could not "
            "produce a statement (provider error). Do NOT synthesise an answer — "
            "report the outage."
        )
    return (
        f"NOTE: {', '.join(silent)} were invited but did not post "
        "(provider error). Base the outcome only on those who did."
    )


def _synthesis_messages(
    state: ForumState,
    question: str,
    thread_text: str,
    final_text: str,
    signals: dict,
    *,
    forced: bool,
    outage_note: str,
) -> list[dict]:
    return [
        {"role": "system", "content": registry.MODERATOR_PROMPT},
        {
            "role": "user",
            "content": (
                "SYNTHESISE THIS THREAD.\n\n"
                f"TOPIC: {state.get('title') or question}\n"
                f"QUESTION: {question}\n\n"
                f"THE THREAD:\n{thread_text}\n\n"
                f"FINAL ANSWERS (after the revision pass — these are what the "
                f"topic concludes with):\n{final_text}\n\n"
                "DETERMINISTIC SIGNALS (computed for you — these are verified, not your opinion):\n"
                f"- converged: {signals.get('converged')}\n"
                f"- largest agreement cluster: {signals.get('largest_cluster')} "
                f"({signals.get('coverage')})\n"
                f"- all clusters: {signals.get('clusters')}\n"
                f"- confidence spread: {signals.get('confidence_spread')}\n"
                f"- unresolved challenges: {signals.get('unresolved_challenges')}\n"
                f"- why: {signals.get('reason')}\n\n"
                + (
                    "The round budget is exhausted, so this is your FINAL synthesis. "
                    "Report the real state of the discussion.\n\n"
                    if forced
                    else ""
                )
                + (f"{outage_note}\n\n" if outage_note else "")
                + 'If converged is false, do NOT publish a single consensus answer — '
                "give each surviving position its actual backers instead."
            ),
        },
    ]


async def _ask_moderator(messages: list[dict], question: str, signals: dict) -> dict:
    """Call the Moderator, falling back to a deterministic synthesis.

    A topic must always end with an outcome; a provider outage produces the
    fallback rather than an error.
    """
    try:
        response = await llm_complete(
            messages, role=MODERATOR_ROLE, temperature=0.25, max_tokens=3072
        )
        data = extract_json(response.content)
        if not data:
            raise ValueError("no parseable JSON in moderator output")
        synthesis = registry.ForumSynthesis(**data).model_dump()
    except Exception as e:  # noqa: BLE001 - a topic must still conclude
        logger.error("forum_synthesis_failed", error=str(e)[:160])
        synthesis = _fallback_synthesis(question, signals)

    # The fallback predates these fields; keep the shape identical either way so
    # the UI never has to branch on whether the Moderator was reachable.
    synthesis.setdefault("process", "")
    synthesis.setdefault("changes", [])
    return synthesis


def _attach_finals(synthesis: dict, finals: list[dict]) -> None:
    """Merge the derived finals with the Moderator's reasons.

    The answers stay exactly as the characters wrote them; only the "why" comes
    from the Moderator, and only for characters that actually moved.
    """
    reasons = {
        c.get("author"): c.get("reason")
        for c in (synthesis.get("changes") or [])
        if isinstance(c, dict)
    }
    for final in finals:
        final["changed_because"] = (
            reasons.get(final["author"], "") if final["changed"] else ""
        )
    synthesis["final_answers"] = finals


async def _publish_synthesis(
    topic_id: str, state: ForumState, synthesis: dict, outcome: str, signals: dict
) -> None:
    """Write the closing post and close the topic, in one transaction."""
    from db.postgres import async_session
    from uuid import UUID

    body = (
        synthesis.get("answer", "")
        if outcome == "consensus"
        else "The Council did not converge. These are the positions that survived."
    )

    async with async_session() as db:
        await append_post(
            db,
            UUID(topic_id),
            "moderator",
            body,
            author_kind="moderator",
            stance="synthesize",
            round=state.get("round", 0),
            kind="synthesis",
            claims=[
                {"text": p.get("position", ""), "sources": p.get("backers", [])}
                for p in synthesis.get("positions", [])
            ],
            extra={"synthesis": synthesis},
            commit=False,
        )
        await conclude_topic(
            db, UUID(topic_id), outcome=outcome, synthesis=synthesis, signals=signals,
            commit=False,
        )
        await db.commit()


async def moderator_synthesis(state: ForumState) -> dict:
    """Read the whole thread and produce the outcome.

    This is where a topic earns one of two shapes: a single accepted answer
    signed by its backers, or a small set of distinct attributed positions.
    Which one is the Moderator's call — but `forum_signals.enforce` gets a veto
    on claiming consensus when the thread demonstrably has not converged.

    The pieces are separate because each answers a different question: what the
    thread said, what the finals are, what to tell the Moderator, how to ask it,
    and how to publish the result.
    """
    from db.postgres import async_session
    from uuid import UUID

    question = state.get("question", "")
    topic_id = state.get("topic_id")

    async with async_session() as db:
        posts = await list_posts(db, UUID(topic_id))

    signals = forum_signals.compute(posts)

    # Derived from the posts, never authored by the Moderator.
    finals = final_answers(posts)

    # `forced` means "the round budget ran out" — the moderator is told to
    # report the real state rather than assume another round will fix it.
    forced = bool(state.get("forced")) or (
        state.get("round", 0) >= state.get("max_rounds", 1)
    )

    # Exhausting the budget does NOT mean plurality. It only means "stop
    # hoping they will converge". A thread that converged before the budget ran
    # out has still converged, and must be publishable as consensus.
    forced_mode = "plurality" if (forced and not signals.get("converged")) else None

    messages = _synthesis_messages(
        state, question,
        _thread_for_moderator(posts),
        _finals_for_moderator(finals),
        signals,
        forced=forced,
        outage_note=_outage_note(state.get("cast") or [], signals),
    )

    synthesis = await _ask_moderator(messages, question, signals)

    if forced_mode:
        synthesis["mode"] = forced_mode

    _attach_finals(synthesis, finals)

    outcome, override_note = forum_signals.enforce(
        signals, synthesis.get("mode", "plurality")
    )
    synthesis["mode"] = outcome
    if override_note:
        synthesis["signals_note"] = (
            f"{synthesis.get('signals_note', '')} | {override_note}"
        ).strip(" |")
    synthesis["signals"] = signals

    await _publish_synthesis(topic_id, state, synthesis, outcome, signals)

    logger.info(
        "forum_synthesis_complete",
        topic_id=topic_id,
        outcome=outcome,
        converged=signals.get("converged"),
        forced=forced,
        override=bool(override_note),
    )
    return {
        "signals": signals,
        "synthesis": synthesis,
        "outcome": outcome,
        "converged": bool(signals.get("converged")),
        "should_continue": False,
        "status": "concluded",
    }


# ======================================================================================
# Control flow
# ======================================================================================


def decide_continue(state: ForumState) -> str:
    """Run another exchange round, or move to the revision pass.

    The bound is `max_rounds`, checked here and nowhere else, so a cast that
    disagrees forever still terminates.

    Both exits go to `revise`, never directly to synthesis: a character's last
    exchange reply is not their final answer, and the Moderator cannot summarise
    finals that do not exist. Synthesis still derives `forced` itself and
    reports the real state of the discussion.
    """
    round_no = state.get("round", 0)
    max_rounds = state.get("max_rounds", 1)

    if state.get("no_speakers"):
        # Nobody has an open challenge. Running the round again would invite
        # the same cast to restate themselves at full price.
        return "revise"

    if round_no >= max_rounds:
        return "revise"

    signals = state.get("signals") or {}
    if signals.get("converged"):
        # Converged early — no point burning another round of calls.
        return "revise"

    return "exchange"


def build_workflow():
    from langgraph.graph import StateGraph, END

    workflow = StateGraph(ForumState)

    workflow.add_node("open_topic", open_topic)
    workflow.add_node("statements", statements)
    workflow.add_node("exchange", exchange)
    workflow.add_node("revise", revise)
    workflow.add_node("synthesis", moderator_synthesis)

    workflow.set_entry_point("open_topic")
    workflow.add_edge("open_topic", "statements")
    workflow.add_edge("statements", "exchange")

    # The discussion loop always exits into a revision pass, never straight
    # into synthesis. That pass is what produces a per-character final answer;
    # going straight to synthesis was how a "final answer" could be missing.
    workflow.add_conditional_edges(
        "exchange",
        decide_continue,
        {"exchange": "exchange", "revise": "revise"},
    )
    workflow.add_edge("revise", "synthesis")
    workflow.add_edge("synthesis", END)

    return workflow


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_workflow().compile()
        logger.info("forum_graph_compiled")
    return _graph


async def run_topic(
    question: str,
    *,
    topic_id: Optional[str] = None,
    cast: Optional[list[str]] = None,
    mode: str = "auto",
    tags: Optional[list] = None,
    origin: str = "user",
    session_id: Optional[str] = None,
    max_rounds: Optional[int] = None,
    on_event=None,
) -> dict:
    """Open a topic and run it to its conclusion.

    `topic_id` reuses a topic row the caller already created (so a client can
    subscribe before the run starts) instead of making a second one.

    `on_event(event_type, payload)` is called as the thread develops so the
    WebSocket layer can stream posts out live. It may be async.
    """
    import asyncio

    async def emit(kind: str, payload: dict):
        if on_event is None:
            return
        try:
            result = on_event(kind, payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.warning("forum_event_emit_failed", kind=kind, error=str(e)[:120])

    initial: ForumState = {
        "topic_id": topic_id,
        "question": question,
        "cast": cast or [],
        "mode": mode,
        "tags": tags or [],
        "origin": origin,
        "session_id": session_id,
        "posts": [],
        "round": 0,
        "round_count": 0,
    }
    if max_rounds is not None:
        initial["max_rounds"] = max_rounds

    graph = get_graph()

    # A fan-out run would break the ordered exchange, so stream node by node.
    final: dict = {}
    async for update in graph.astream(initial, stream_mode="updates"):
        for node_name, node_output in update.items():
            if not isinstance(node_output, dict):
                continue
            final.update(node_output)
            await emit(node_name, node_output)

    return final