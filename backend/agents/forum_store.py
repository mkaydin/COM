"""Persistence for forum topics and posts.

Every post write goes through `append_post`, which is the only place `seq` is
assigned. That matters: `posts(topic_id, seq)` is UNIQUE, and the `exchange`
node has several characters writing concurrently. Rather than trusting
callers to serialize, the sequence is allocated under a row lock on the topic
(`SELECT ... FOR UPDATE`), so concurrent writers queue at the database instead
of colliding and raising `IntegrityError`.

Posts are also returned in a plain-dict shape — the graph, the signals module
and the WebSocket layer all work on dicts, not ORM objects, which keeps them
testable without a database.
"""

import re
from typing import Any, Optional

from sqlalchemy import select, func, desc, asc
from uuid import uuid4

from db.forum_models import Topic, Post, TopicIntake
from logging_config import get_logger

logger = get_logger(__name__)

MODERATOR = "moderator"
USER = "user"


# --- serialisation -----------------------------------------------------------------


def post_to_dict(p: Post, *, include_reply_to_seq: bool = False) -> dict:
    """An ORM Post as the dict the rest of the forum uses."""
    out: dict[str, Any] = {
        "id": str(p.id),
        "topic_id": str(p.topic_id),
        "seq": p.seq,
        "round": p.round,
        # `kind` may be NULL on rows written before the column existed; they
        # were all replies or openings, and the UI treats "reply" as the
        # ordinary case, so the fallback is harmless rather than a guess.
        "kind": p.kind or "reply",
        "author_kind": p.author_kind,
        "author": p.author,
        "body": p.body,
        "stance": p.stance,
        "reply_to": str(p.reply_to) if p.reply_to else None,
        "revision_of": str(p.revision_of) if p.revision_of else None,
        "confidence": p.confidence,
        "claims": p.claims or [],
        "citations": p.citations or [],
        "extra": p.extra or {},
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
    if include_reply_to_seq:
        out["reply_to_seq"] = p.extra.get("reply_to_seq") if p.extra else None
    return out


#: Posts that count as a character's own answer, as opposed to the Moderator's
#: or the user's.
_ANSWER_KINDS = ("statement", "reply", "revision")

#: Jaccard similarity below which a revision counts as a real change of position.
#:
#: Calibrated against real output rather than guessed:
#:
#:   genuine revisions from a debate   0.135 - 0.208
#:   restatements "without changing"   0.50 - 0.66  (model output)
#:   an aggressive human rewrite       0.341
#:
#: 0.30 sits below every restatement measured and above every real revision.
#:
#: Known limit: a reversal that reuses the same wording ("we should adopt X
#: because A, B" -> "we should not adopt X because A, B") scores high and is
#: reported as unchanged. Lexical overlap cannot see negation. The Moderator's
#: `changes` list is the cross-check for that case, and both are shown.
_CHANGED_THRESHOLD = 0.30


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(w) > 2}


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def final_answers(posts: list[dict]) -> list[dict]:
    """One entry per character: their revision, else their latest post.

    A character who never revised still has a final answer — their last word in
    the thread. A debate must not end with a character silently absent just
    because their revision call failed.

    `changed` is deterministic, not the Moderator's opinion: it compares the
    opening against the final with token overlap, and reports True when the
    revision differs enough to be a new position rather than the same one
    rephrased.
    """
    by_author: dict[str, list[dict]] = {}
    for post in posts:
        if post.get("author_kind") != "character":
            continue
        # Rows written before `kind` existed have no value here; they were
        # openings and replies, and defaulting to "reply" keeps them counted.
        if (post.get("kind") or "reply") not in _ANSWER_KINDS:
            continue
        by_author.setdefault(post["author"], []).append(post)

    out: list[dict] = []
    for author, mine in by_author.items():
        mine = sorted(mine, key=lambda p: p["seq"])
        opening = next((p for p in mine if p.get("kind") == "statement"), mine[0])
        final = next(
            (p for p in reversed(mine) if p.get("kind") == "revision"), mine[-1]
        )
        revised = final["seq"] != opening["seq"]
        out.append({
            "author": author,
            "seq": final["seq"],
            "body": final["body"],
            "stance": final["stance"],
            "confidence": final.get("confidence"),
            "kind": final.get("kind"),
            "revision_of": final.get("revision_of"),
            "opening_seq": opening["seq"],
            "opening_body": opening["body"],
            # A revision that restates the opening is not a change.
            "changed": bool(
                revised and _similarity(opening["body"], final["body"]) < _CHANGED_THRESHOLD
            ),
        })

    out.sort(key=lambda r: r["seq"])
    return out


def topic_markdown(topic: dict, posts: list[dict]) -> str:
    """Render a concluded topic for the library.

    Finals first, because that is what a reader wants and what retrieval
    matches on; the full transcript follows so the reasoning is not lost. The
    document is ordinary Markdown so it stays readable in the library and
    chunks sensibly on ingestion.
    """
    finals = final_answers(posts)
    synthesis = topic.get("synthesis") or {}

    lines: list[str] = [f"# {topic.get('title') or topic.get('question') or 'Forum topic'}", ""]
    lines += [f"**Question:** {topic.get('question') or ''}", ""]
    if topic.get("framing"):
        lines += [str(topic["framing"]).strip(), ""]
    if topic.get("outcome"):
        lines += [f"**Outcome:** {topic['outcome']}", ""]

    if finals:
        lines += ["## Final answers", ""]
        for final in finals:
            lines += [f"### {final['author'].title()}", ""]
            lines += [(final["body"] or "").strip(), ""]
            if final["changed"] and final.get("changed_because"):
                lines += [f"_Changed position: {final['changed_because']}_", ""]
            elif final["changed"]:
                lines += ["_Changed position during the discussion._", ""]
            else:
                lines += ["_Maintained their opening position._", ""]

    lines += ["## Discussion", ""]
    for post in posts:
        lines += [
            f"### #{post['seq']} — {post['author'].title()} [{post['stance']}]",
            "",
            (post.get("body") or "").strip(),
            "",
        ]

    if synthesis.get("answer") or synthesis.get("positions"):
        lines += ["## Moderator synthesis", ""]
        if synthesis.get("answer"):
            lines += [str(synthesis["answer"]).strip(), ""]
        for position in synthesis.get("positions") or []:
            backers = ", ".join(position.get("backers") or [])
            heading = position.get("label") or "Position"
            lines += [f"**{heading}**" + (f" — {backers}" if backers else ""), ""]
            lines += [(position.get("position") or "").strip(), ""]
        if synthesis.get("fault_line"):
            lines += [f"**Fault line:** {synthesis['fault_line']}", ""]
        if synthesis.get("process"):
            lines += ["### What happened in the discussion", "", synthesis["process"], ""]

    seen: set[str] = set()
    citations: list[str] = []
    for post in posts:
        for citation in post.get("citations") or []:
            url = citation.get("url") or ""
            if url and url not in seen:
                seen.add(url)
                citations.append(f"- [{citation.get('title') or url}]({url})")
    if citations:
        lines += ["## Sources", ""] + citations + [""]

    return "\n".join(lines).strip() + "\n"


def topic_to_dict(t: Topic, *, with_posts: bool = False, posts: Optional[list] = None) -> dict:
    out: dict[str, Any] = {
        "id": str(t.id),
        "session_id": str(t.session_id) if t.session_id else None,
        "deliberation_id": str(t.deliberation_id) if t.deliberation_id else None,
        "title": t.title,
        "question": t.question,
        "framing": t.framing,
        "origin": t.origin,
        "status": t.status,
        # Only meaningful when status == "failed", but always present so the UI
        # does not have to branch on the shape of the topic.
        "error": getattr(t, "error", None),
        "mode": t.mode,
        "outcome": t.outcome,
        "cast": t.cast or [],
        "tags": t.tags or [],
        "source_meta": t.source_meta or {},
        "synthesis": t.synthesis or {},
        "signals": t.signals or {},
        "max_rounds": t.max_rounds,
        "round_count": t.round_count,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "concluded_at": t.concluded_at.isoformat() if t.concluded_at else None,
    }
    if with_posts:
        # Callers pass either ORM rows (straight from a query) or the dicts
        # `list_posts` already returns. Accept both: serialising a dict would
        # raise `'dict' object has no attribute 'id'` at request time.
        serialised = [
            p if isinstance(p, dict) else post_to_dict(p) for p in (posts or [])
        ]
        out["posts"] = serialised
        # Derived here so every caller — API, WebSocket, save-to-library —
        # agrees on what a character finally said. Deriving it in the UI would
        # mean three implementations drifting apart.
        out["final_answers"] = final_answers(serialised)
    return out


# --- writes ------------------------------------------------------------------------


async def append_post(
    db,
    topic_id,
    author: str,
    body: str,
    *,
    author_kind: str = "character",
    stance: str = "propose",
    round: int = 0,
    kind: str = "reply",
    reply_to: Optional[str] = None,
    reply_to_seq: Optional[int] = None,
    revision_of: Optional[str] = None,
    confidence: Optional[float] = None,
    claims: Optional[list] = None,
    citations: Optional[list] = None,
    extra: Optional[dict] = None,
    commit: bool = True,
) -> dict:
    """Append one post, allocating `seq` under a lock on the topic row.

    `reply_to_seq` is stored in `extra` so the signal layer can resolve
    "which post was this challenging" without a second query per post.
    """
    # Lock the topic row so concurrent writers serialize on the sequence.
    await db.execute(
        select(Topic.id).where(Topic.id == topic_id).with_for_update()
    )

    next_seq = await db.scalar(
        select(func.coalesce(func.max(Post.seq), 0) + 1).where(Post.topic_id == topic_id)
    )

    extra = dict(extra or {})
    if reply_to_seq is not None:
        extra["reply_to_seq"] = reply_to_seq

    post = Post(
        topic_id=topic_id,
        seq=int(next_seq),
        round=round,
        kind=kind,
        author_kind=author_kind,
        author=author,
        body=body,
        stance=stance,
        reply_to=reply_to,
        revision_of=revision_of,
        confidence=confidence,
        claims=claims or [],
        citations=citations or [],
        extra=extra,
    )
    db.add(post)

    if commit:
        await db.commit()
        await db.refresh(post)
    else:
        await db.flush()
        await db.refresh(post)

    logger.info(
        "forum_post_appended",
        topic_id=str(topic_id),
        seq=post.seq,
        author=author,
        stance=stance,
        round=round,
    )
    return post_to_dict(post, include_reply_to_seq=True)


async def create_topic(
    db,
    question: str,
    *,
    title: Optional[str] = None,
    framing: Optional[str] = None,
    origin: str = "user",
    mode: str = "auto",
    cast: Optional[list] = None,
    tags: Optional[list] = None,
    session_id: Optional[str] = None,
    source_meta: Optional[dict] = None,
    max_rounds: int = 2,
    topic_id: Optional[str] = None,
    commit: bool = True,
) -> dict:
    """Create a topic, or update one the caller already created.

    `topic_id` exists because the API creates the row up front so a client can
    subscribe over WebSocket before the run begins. The Moderator's node then
    fills in the title/framing/cast rather than inserting a second, orphan
    topic — the returned id is the caller's id in both cases.
    """
    values = dict(
        title=title or question[:120],
        question=question,
        framing=framing,
        origin=origin,
        mode=mode,
        status="open",
        cast=cast or [],
        tags=tags or [],
        source_meta=source_meta or {},
        max_rounds=max_rounds,
        session_id=session_id,
    )

    if topic_id is not None:
        topic = await get_topic(db, topic_id)
        if topic is not None:
            for key, value in values.items():
                if value in (None, [], {}) and key not in ("cast", "tags"):
                    continue
                setattr(topic, key, value)
            if commit:
                await db.commit()
            else:
                await db.flush()
            await db.refresh(topic)
            return topic_to_dict(topic)

    topic = Topic(id=topic_id or uuid4(), **values)
    db.add(topic)
    if commit:
        await db.commit()
    else:
        await db.flush()
    await db.refresh(topic)
    logger.info("forum_topic_created", topic_id=str(topic.id), origin=origin, mode=mode)
    return topic_to_dict(topic)


async def get_topic(db, topic_id) -> Optional[Topic]:
    return (await db.execute(select(Topic).where(Topic.id == topic_id))).scalar_one_or_none()


async def list_posts(db, topic_id, *, since_seq: Optional[int] = None, limit: int = 500) -> list[dict]:
    stmt = select(Post).where(Post.topic_id == topic_id).order_by(asc(Post.seq)).limit(limit)
    if since_seq is not None:
        stmt = stmt.where(Post.seq > since_seq)
    rows = (await db.execute(stmt)).scalars().all()
    return [post_to_dict(p, include_reply_to_seq=True) for p in rows]


async def list_topics(
    db,
    *,
    status: Optional[str] = None,
    outcome: Optional[str] = None,
    tag: Optional[str] = None,
    cast: Optional[str] = None,
    session_id=None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    stmt = select(Topic)
    if status:
        stmt = stmt.where(Topic.status == status)
    if outcome:
        stmt = stmt.where(Topic.outcome == outcome)
    if session_id:
        stmt = stmt.where(Topic.session_id == session_id)
    if cast:
        stmt = stmt.where(Topic.cast.contains([cast]))

    if tag:
        # Must be part of the query, not a filter applied after it: the LIMIT
        # runs first, so dropping non-matching rows in Python silently returns
        # a short page — and an empty one for a tag that exists on an older
        # topic outside the window.
        stmt = stmt.where(Topic.tags.contains([tag]))

    stmt = stmt.order_by(desc(Topic.created_at)).limit(limit).offset(offset)

    rows = (await db.execute(stmt)).scalars().all()

    # Post counts for the whole page in one grouped query. The forum list
    # renders them on every row, and a per-row count would be an N+1 against
    # a table that only ever grows.
    counts: dict = {}
    if rows:
        ids = [t.id for t in rows]
        grouped = await db.execute(
            select(Post.topic_id, func.count(Post.id))
            .where(Post.topic_id.in_(ids))
            .group_by(Post.topic_id)
        )
        counts = {topic_id: n for topic_id, n in grouped.all()}

    return [
        {**topic_to_dict(t), "post_count": counts.get(t.id, 0)}
        for t in rows
    ]


async def count_posts(db, topic_id) -> int:
    return int(await db.scalar(select(func.count(Post.id)).where(Post.topic_id == topic_id)) or 0)


async def conclude_topic(
    db,
    topic_id,
    *,
    outcome: str,
    synthesis: dict,
    signals: dict,
    commit: bool = True,
) -> dict:
    """Mark a thread closed with its outcome and synthesis."""
    from datetime import datetime, timezone

    topic = await get_topic(db, topic_id)
    if topic is None:
        raise ValueError(f"Topic {topic_id} not found")
    topic.status = "concluded"
    topic.outcome = outcome
    topic.synthesis = synthesis
    topic.signals = signals
    topic.concluded_at = datetime.now(timezone.utc)
    if commit:
        await db.commit()
    else:
        await db.flush()
    await db.refresh(topic)
    logger.info("forum_topic_concluded", topic_id=str(topic_id), outcome=outcome)
    return topic_to_dict(topic)


async def fail_topic(db, topic_id, reason: str, commit: bool = True) -> dict | None:
    """Mark a thread as failed, with the reason.

    A run that raises leaves `status = "debating"` with nothing scheduled to
    change it, so the thread sits there forever with no way to tell a provider
    outage from a crash — and `POST /close` on it synthesises from an empty
    half-written round. Recording the failure is what makes it recoverable:
    the UI can show the reason, and the topic can be reopened.

    Never raises. This runs in the error path of an already-failing run, so a
    second failure here must not mask the first.
    """
    try:
        topic = await get_topic(db, topic_id)
        if topic is None:
            logger.warning("forum_fail_topic_missing", topic_id=str(topic_id))
            return None
        topic.status = "failed"
        topic.error = (reason or "")[:2000]
        if commit:
            await db.commit()
        else:
            await db.flush()
        logger.warning("forum_topic_failed", topic_id=str(topic_id), reason=topic.error[:200])
        return topic_to_dict(topic)
    except Exception as e:  # pragma: no cover - defensive
        logger.error(
            "forum_fail_topic_error", topic_id=str(topic_id), error=str(e)[:200]
        )
        return None


async def clear_topic_error(db, topic_id, commit: bool = True) -> None:
    """Clear a previous failure when a topic starts running again."""
    try:
        topic = await get_topic(db, topic_id)
        if topic is not None and topic.error:
            topic.error = None
            if commit:
                await db.commit()
            else:
                await db.flush()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("forum_clear_error_failed", topic_id=str(topic_id), error=str(e)[:160])


async def bump_round(db, topic_id, commit: bool = True) -> int:
    topic = await get_topic(db, topic_id)
    if topic is None:
        raise ValueError(f"Topic {topic_id} not found")
    topic.round_count = (topic.round_count or 0) + 1
    if commit:
        await db.commit()
    else:
        await db.flush()
    return topic.round_count


# --- intake queue ------------------------------------------------------------------


async def enqueue_intake(
    db,
    question: str,
    *,
    source: Optional[str] = None,
    payload: Optional[dict] = None,
    options: Optional[dict] = None,
    commit: bool = True,
) -> dict:
    row = TopicIntake(
        question=question,
        source=source,
        payload=payload or {},
        options=options or {},
        status="queued",
    )
    db.add(row)
    if commit:
        await db.commit()
    else:
        await db.flush()
    await db.refresh(row)
    logger.info("forum_intake_queued", intake_id=str(row.id), source=source)
    return {
        "id": str(row.id),
        "status": row.status,
        "question": row.question,
        "source": row.source,
        "received_at": row.received_at.isoformat() if row.received_at else None,
    }


async def release_intake(db, intake_id, commit: bool = True) -> bool:
    """Return a claimed item to the queue.

    `claim_intake` commits `status = 'opened'` *before* the caller has created
    a topic or scheduled the run. Anything that fails in between — topic
    creation, background-task scheduling, a process restart — therefore left the
    question marked `opened` with no topic and nothing left to pick it up: the
    question was lost permanently, and `list_intake(status='queued')` no longer
    showed it.

    Putting it back is the only safe recovery. Guarded on `status = 'opened'`
    so a concurrent drain that legitimately claimed it is not undone.
    """
    row = (
        await db.execute(
            select(TopicIntake)
            .where(TopicIntake.id == intake_id)
            .where(TopicIntake.status == "opened")
        )
    ).scalar_one_or_none()
    if row is None:
        return False

    row.status = "queued"
    row.opened_at = None
    row.topic_id = None
    if commit:
        await db.commit()
    else:
        await db.flush()
    logger.warning("forum_intake_released", intake_id=str(intake_id))
    return True


async def claim_intake(db, intake_id) -> Optional[dict]:
    """Mark a queued item as opened and return it.

    A conditional UPDATE rather than an upsert: `pg_insert(...).on_conflict_do_update`
    would still validate NOT NULL columns on the INSERT path, and `question` is
    NOT NULL. `WHERE status = 'queued'` is also what stops two workers from
    both claiming the same item — the loser matches zero rows.
    """
    from datetime import datetime, timezone

    row = (
        await db.execute(
            select(TopicIntake)
            .where(TopicIntake.id == intake_id)
            .where(TopicIntake.status == "queued")
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return None

    row.status = "opened"
    row.opened_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)

    return {
        "id": str(row.id),
        "question": row.question,
        "source": row.source,
        "payload": row.payload or {},
        "options": row.options or {},
    }


async def list_intake(db, *, status: Optional[str] = "queued", limit: int = 50) -> list[dict]:
    stmt = select(TopicIntake).order_by(asc(TopicIntake.received_at)).limit(limit)
    if status:
        stmt = stmt.where(TopicIntake.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": str(r.id),
            "status": r.status,
            "question": r.question,
            "source": r.source,
            "payload": r.payload or {},
            "options": r.options or {},
            "topic_id": str(r.topic_id) if r.topic_id else None,
            "received_at": r.received_at.isoformat() if r.received_at else None,
        }
        for r in rows
    ]


async def reject_intake(db, intake_id, reason: Optional[str] = None) -> bool:
    row = (await db.execute(select(TopicIntake).where(TopicIntake.id == intake_id))).scalar_one_or_none()
    if row is None:
        return False
    row.status = "rejected"
    row.reject_reason = reason
    await db.commit()
    logger.info("forum_intake_rejected", intake_id=str(intake_id), reason=reason)
    return True


async def link_intake_to_topic(db, intake_id, topic_id) -> None:
    row = (await db.execute(select(TopicIntake).where(TopicIntake.id == intake_id))).scalar_one_or_none()
    if row is None:
        return
    row.topic_id = topic_id
    await db.commit()