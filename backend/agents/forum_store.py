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

from typing import Any, Optional

from sqlalchemy import select, func, desc, asc
from sqlalchemy.dialects.postgresql import insert as pg_insert

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
        "author_kind": p.author_kind,
        "author": p.author,
        "body": p.body,
        "stance": p.stance,
        "reply_to": str(p.reply_to) if p.reply_to else None,
        "confidence": p.confidence,
        "claims": p.claims or [],
        "citations": p.citations or [],
        "extra": p.extra or {},
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
    if include_reply_to_seq:
        out["reply_to_seq"] = p.extra.get("reply_to_seq") if p.extra else None
    return out


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
        out["posts"] = [post_to_dict(p) for p in (posts or [])]
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
    reply_to: Optional[str] = None,
    reply_to_seq: Optional[int] = None,
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
        author_kind=author_kind,
        author=author,
        body=body,
        stance=stance,
        reply_to=reply_to,
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
    commit: bool = True,
) -> dict:
    topic = Topic(
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
    stmt = stmt.order_by(desc(Topic.created_at)).limit(limit).offset(offset)

    rows = (await db.execute(stmt)).scalars().all()
    out = []
    for t in rows:
        d = topic_to_dict(t)
        if tag and tag not in (t.tags or []):
            continue
        out.append(d)
    return out


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


async def claim_intake(db, intake_id) -> Optional[dict]:
    """Mark a queued item as opened and return it.

    Uses a conditional UPDATE so two workers cannot both claim the same item.
    """
    from datetime import datetime, timezone

    result = await db.execute(
        pg_insert(TopicIntake)
        .values(id=intake_id, status="opened", opened_at=datetime.now(timezone.utc))
        .on_conflict_do_update(
            index_elements=[TopicIntake.id],
            set_={"status": "opened", "opened_at": datetime.now(timezone.utc)},
            where=(TopicIntake.status == "queued"),
        )
        .returning(TopicIntake.id, TopicIntake.question, TopicIntake.source,
                   TopicIntake.payload, TopicIntake.options)
    )
    row = result.first()
    if row is None:
        return None
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