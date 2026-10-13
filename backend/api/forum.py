"""Forum API — topics as threads, and the API intake path.

Two ways in:

* `POST /api/forum/topics` — a person opens a topic in the UI.
* `POST /api/forum/intake` — an external system drops a question in. This is
  the "or from api" path: the question lands in the intake queue and becomes
  a topic, either immediately or when the Moderator drains the queue.

Both end up in the same place. Intake just adds provenance and the option of
queueing rather than blocking on a full thread run.
"""

import uuid
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from uuid import UUID

from db.postgres import get_db, async_session
from db.forum_models import Topic, Post
from agents import registry
from agents.forum_store import (
    create_topic,
    list_posts,
    list_topics,
    get_topic,
    topic_to_dict,
    post_to_dict,
    append_post,
    enqueue_intake,
    list_intake,
    reject_intake,
    claim_intake,
    link_intake_to_topic,
    count_posts,
)
from logging_config import get_logger

router = APIRouter(prefix="/api/forum", tags=["forum"])
logger = get_logger(__name__)


# ======================================================================================
# Schemas
# ======================================================================================


class TopicCreate(BaseModel):
    question: str = Field(..., min_length=1, max_length=10000)
    #: 'auto' lets the Moderator decide; 'consensus'/'diverse' override it.
    mode: str = "auto"
    #: Empty means "the Moderator picks".
    cast: List[str] = []
    tags: List[str] = []
    session_id: Optional[str] = None
    #: Run inline (wait for the outcome) or return immediately and stream via WS.
    stream: bool = True
    max_rounds: Optional[int] = Field(default=None, ge=1, le=6)


class IntakeCreate(BaseModel):
    question: str = Field(..., min_length=1, max_length=10000)
    source: Optional[str] = Field(default=None, max_length=128)
    payload: dict = {}
    options: dict = {}
    #: Open a topic right away (default) or leave it queued for the Moderator.
    open_now: bool = True


class UserReply(BaseModel):
    body: str = Field(..., min_length=1, max_length=20000)
    #: Ask the Council to keep going after this post.
    trigger_round: bool = True


class TopicPatch(BaseModel):
    title: Optional[str] = None
    mode: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = None


# ======================================================================================
# Helpers
# ======================================================================================


def _forum_enabled() -> None:
    from config import get_settings

    if not get_settings().forum_enabled:
        raise HTTPException(status_code=404, detail="Forum is disabled (FORUM_ENABLED=false)")


def _require_intake_key(x_com_key: Optional[str]) -> None:
    """Gate intake behind an optional shared secret.

    An empty `FORUM_INTAKE_KEY` means local-only, which is the default — a
    forum that accepts questions from the open internet by default is not a
    reasonable thing to ship.
    """
    from config import get_settings

    s = get_settings()
    if not s.forum_intake_key:
        return
    if x_com_key != s.forum_intake_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-COM-Key")


async def _run_and_respond(topic_id: str, question: str, *, cast, mode, tags, origin, session_id, max_rounds):
    from agents.forum_graph import run_topic

    return await run_topic(
        question,
        cast=cast,
        mode=mode,
        tags=tags,
        origin=origin,
        session_id=session_id,
        max_rounds=max_rounds,
    )


# ======================================================================================
# Cast / discovery
# ======================================================================================


@router.get("/cast")
async def get_cast():
    """Every registered character, with its live model binding."""
    from llm import roles, providers

    try:
        provider = providers.get()
        bindings = {r["role"]: r for r in roles.all_roles(provider)}
    except Exception:
        bindings = {}

    out = []
    for c in registry.all_characters():
        card = c.brief()
        card["model"] = bindings.get(c.model_role, {}).get("model")
        out.append(card)
    return {"cast": out, "order": registry.ORDER}


@router.get("/characters")
async def list_characters():
    return {"characters": registry.briefs()}


# ======================================================================================
# Topics
# ======================================================================================


@router.post("/topics", status_code=202)
async def open_topic(body: TopicCreate, background_tasks: BackgroundTasks,
                     db: AsyncSession = Depends(get_db)):
    """Open a topic and run it.

    Returns 202 with the topic id immediately; the thread develops in the
    background and is streamed over `WS /ws/forum/{topic_id}`. Pass
    `stream=false` to block until the outcome instead.
    """
    _forum_enabled()

    cast: list[str] = []
    if body.cast:
        try:
            cast = registry.validate_cast(body.cast)
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    mode = body.mode if body.mode in ("auto", "consensus", "diverse") else "auto"

    async with async_session() as sdb:
        topic = await create_topic(
            sdb,
            body.question,
            origin="user",
            mode=mode,
            cast=cast,
            tags=body.tags,
            session_id=body.session_id,
            max_rounds=body.max_rounds or 2,
        )

    logger.info("forum_topic_requested", topic_id=topic["id"], cast=cast, mode=mode)

    if body.stream:
        background_tasks.add_task(
            _run_and_respond,
            topic["id"], body.question, cast=cast, mode=mode, tags=body.tags,
            origin="user", session_id=body.session_id, max_rounds=body.max_rounds,
        )
        return {"topic_id": topic["id"], "status": "accepted", "mode": mode, "cast": cast}

    result = await _run_and_respond(
        topic["id"], body.question, cast=cast, mode=mode, tags=body.tags,
        origin="user", session_id=body.session_id, max_rounds=body.max_rounds,
    )
    async with async_session() as sdb:
        t = await get_topic(sdb, UUID(topic["id"]))
        posts = await list_posts(sdb, UUID(topic["id"])) if t else []
        return {
            "topic_id": topic["id"],
            "status": (t.status if t else "unknown"),
            "topic": topic_to_dict(t) if t else None,
            "posts": posts,
            "outcome": result.get("outcome"),
            "signals": result.get("signals"),
            "synthesis": result.get("synthesis"),
        }


@router.get("/topics")
async def list_all_topics(
    status: Optional[str] = None,
    outcome: Optional[str] = None,
    tag: Optional[str] = None,
    cast: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    _forum_enabled()
    topics = await list_topics(
        db, status=status, outcome=outcome, tag=tag, cast=cast,
        session_id=session_id, limit=limit, offset=offset,
    )
    return {"topics": topics, "count": len(topics), "limit": limit, "offset": offset}


@router.get("/topics/{topic_id}")
async def get_one_topic(topic_id: UUID, since_seq: Optional[int] = None,
                        db: AsyncSession = Depends(get_db)):
    """A topic with its posts.

    `since_seq` returns only newer posts — the catch-up path for a client
    reconnecting mid-thread.
    """
    _forum_enabled()
    topic = await get_topic(db, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    posts = await list_posts(db, topic_id, since_seq=since_seq)
    return {
        **topic_to_dict(topic, with_posts=True, posts=[]),
        "posts": posts,
        "post_count": await count_posts(db, topic_id),
        "partial": since_seq is not None,
    }


@router.patch("/topics/{topic_id}")
async def patch_topic(topic_id: UUID, body: TopicPatch, db: AsyncSession = Depends(get_db)):
    _forum_enabled()
    topic = await get_topic(db, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    if body.title is not None:
        topic.title = body.title[:500]
    if body.mode is not None:
        if body.mode not in ("auto", "consensus", "diverse"):
            raise HTTPException(status_code=400, detail="mode must be auto|consensus|diverse")
        topic.mode = body.mode
    if body.tags is not None:
        topic.tags = body.tags
    if body.status is not None:
        if body.status not in ("open", "debating", "concluded", "archived"):
            raise HTTPException(status_code=400, detail="invalid status")
        topic.status = body.status

    await db.commit()
    await db.refresh(topic)
    return topic_to_dict(topic)


@router.delete("/topics/{topic_id}", status_code=204)
async def delete_topic(topic_id: UUID, db: AsyncSession = Depends(get_db)):
    _forum_enabled()
    topic = await get_topic(db, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    # posts cascade via the FK's ON DELETE CASCADE
    await db.delete(topic)
    await db.commit()
    logger.info("forum_topic_deleted", topic_id=str(topic_id))


@router.get("/topics/{topic_id}/posts")
async def get_posts(topic_id: UUID, since_seq: Optional[int] = None,
                    limit: int = Query(default=500, ge=1, le=2000),
                    db: AsyncSession = Depends(get_db)):
    _forum_enabled()
    if await get_topic(db, topic_id) is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    posts = await list_posts(db, topic_id, since_seq=since_seq, limit=limit)
    return {"posts": posts, "count": len(posts), "since_seq": since_seq}


@router.post("/topics/{topic_id}/posts", status_code=201)
async def post_reply(topic_id: UUID, body: UserReply,
                     background_tasks: BackgroundTasks,
                     db: AsyncSession = Depends(get_db)):
    """The user joins the thread.

    Their post is written immediately and visible; a following exchange round
    runs in the background so the characters actually respond to it.
    """
    _forum_enabled()
    topic = await get_topic(db, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    post = await append_post(
        db, topic_id, "user", body.body, author_kind="user",
        stance="question", round=topic.round_count or 0,
    )

    if topic.status == "open":
        topic.status = "debating"
        await db.commit()

    if body.trigger_round and topic.cast:
        background_tasks.add_task(_continue_thread, str(topic_id))

    return {"post": post, "post_count": await count_posts(db, topic_id)}


async def _continue_thread(topic_id: str):
    """Run one more exchange round + synthesis on a concluded/open thread."""
    from agents.forum_characters import run_character
    from agents.forum_store import bump_round, conclude_topic
    from agents.forum_graph import moderator_synthesis
    from agents import forum_signals

    async with async_session() as db:
        topic = await get_topic(db, UUID(topic_id))
        if topic is None:
            return
        cast = topic.cast or []
        question = topic.question

    new_posts = []
    async with async_session() as db:
        for key in cast:
            so_far = await list_posts(db, UUID(topic_id))
            result = await run_character(key, question, posts=so_far, is_opening=False)
            if not result:
                continue
            reply_to_seq = result.get("reply_to_seq")
            reply_to_id = None
            if reply_to_seq is not None:
                m = next((p for p in so_far if p.get("seq") == reply_to_seq), None)
                reply_to_id = m["id"] if m else None
            p = await append_post(
                db, UUID(topic_id), key, result["body"], author_kind="character",
                stance=result["stance"], round=(topic.round_count or 0) + 1,
                reply_to=reply_to_id, reply_to_seq=reply_to_seq,
                confidence=result.get("confidence"), claims=result.get("claims"),
                citations=result.get("citations"), extra=result.get("extra"), commit=False,
            )
            new_posts.append(p)
        await bump_round(db, UUID(topic_id), commit=False)
        await db.commit()

    logger.info("forum_thread_continued", topic_id=topic_id, posted=len(new_posts))


@router.post("/topics/{topic_id}/close")
async def close_topic(topic_id: UUID, db: AsyncSession = Depends(get_db)):
    """Force a closing synthesis now, without waiting for the round budget."""
    _forum_enabled()
    topic = await get_topic(db, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    from agents.forum_graph import moderator_synthesis

    posts = await list_posts(db, topic_id)
    out = await moderator_synthesis({
        "topic_id": str(topic_id),
        "question": topic.question,
        "title": topic.title,
        "round": topic.round_count or 0,
        "max_rounds": (topic.round_count or 0) + 1,
        "forced": True,
    })
    return {"topic_id": str(topic_id), "outcome": out.get("outcome"),
            "signals": out.get("signals"), "synthesis": out.get("synthesis"),
            "post_count": len(posts)}


@router.post("/topics/{topic_id}/reopen", status_code=202)
async def reopen_topic(topic_id: UUID, background_tasks: BackgroundTasks,
                       db: AsyncSession = Depends(get_db)):
    _forum_enabled()
    topic = await get_topic(db, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    if not topic.cast:
        raise HTTPException(status_code=400, detail="Topic has no recorded cast")

    topic.status = "debating"
    await db.commit()
    background_tasks.add_task(_continue_thread, str(topic_id))
    return {"topic_id": str(topic_id), "status": "debating"}


# ======================================================================================
# API intake
# ======================================================================================


@router.post("/intake", status_code=202)
async def intake_question(
    body: IntakeCreate,
    background_tasks: BackgroundTasks,
    x_com_key: Optional[str] = Header(default=None, alias="X-COM-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Submit a question from an external system.

    `open_now=true` (the default) opens a topic immediately and runs it.
    `open_now=false` leaves it queued for the Moderator to drain.
    """
    _forum_enabled()
    _require_intake_key(x_com_key)

    entry = await enqueue_intake(
        db, body.question, source=body.source, payload=body.payload, options=body.options
    )
    logger.info("forum_intake_received", intake_id=entry["id"], source=body.source)

    if not body.open_now:
        return {"intake_id": entry["id"], "status": "queued",
                "hint": "POST /api/forum/intake/drain to open queued questions"}

    claimed = await claim_intake(db, UUID(entry["id"]))
    if claimed is None:
        return {"intake_id": entry["id"], "status": "claimed",
                "hint": "another worker already opened this"}

    opts = claimed.get("options") or {}
    cast = opts.get("cast") or []
    if cast:
        try:
            cast = registry.validate_cast(cast)
        except (KeyError, ValueError):
            cast = []

    topic = await create_topic(
        db,
        claimed["question"],
        origin="api",
        mode=opts.get("mode", "auto"),
        cast=cast,
        tags=opts.get("tags") or [],
        source_meta={
            "source": claimed.get("source"),
            "intake_id": claimed["id"],
            "payload": claimed.get("payload"),
        },
    )
    await link_intake_to_topic(db, UUID(claimed["id"]), UUID(topic["id"]))

    background_tasks.add_task(
        _run_and_respond, topic["id"], claimed["question"], cast=cast,
        mode=opts.get("mode", "auto"), tags=opts.get("tags") or [],
        origin="api", session_id=None, max_rounds=opts.get("max_rounds"),
    )

    return {"intake_id": claimed["id"], "topic_id": topic["id"], "status": "opening"}


@router.get("/intake")
async def intake_queue(status: Optional[str] = "queued",
                       limit: int = Query(default=50, ge=1, le=200),
                       x_com_key: Optional[str] = Header(default=None, alias="X-COM-Key"),
                       db: AsyncSession = Depends(get_db)):
    _require_intake_key(x_com_key)
    items = await list_intake(db, status=status, limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/intake/drain")
async def drain_intake(limit: int = Query(default=5, ge=1, le=50),
                       background_tasks: BackgroundTasks = None,
                       x_com_key: Optional[str] = Header(default=None, alias="X-COM-Key"),
                       db: AsyncSession = Depends(get_db)):
    """Open up to `limit` queued questions as topics."""
    _require_intake_key(x_com_key)

    queued = await list_intake(db, status="queued", limit=limit)
    opened = []
    for item in queued:
        claimed = await claim_intake(db, UUID(item["id"]))
        if claimed is None:
            continue
        opts = claimed.get("options") or {}
        cast = []
        if opts.get("cast"):
            try:
                cast = registry.validate_cast(opts["cast"])
            except (KeyError, ValueError):
                cast = []
        topic = await create_topic(
            db, claimed["question"], origin="api", mode=opts.get("mode", "auto"),
            cast=cast, tags=opts.get("tags") or [],
            source_meta={"source": claimed.get("source"), "intake_id": claimed["id"],
                         "payload": claimed.get("payload")},
        )
        await link_intake_to_topic(db, UUID(claimed["id"]), UUID(topic["id"]))
        if background_tasks is not None:
            background_tasks.add_task(
                _run_and_respond, topic["id"], claimed["question"], cast=cast,
                mode=opts.get("mode", "auto"), tags=opts.get("tags") or [],
                origin="api", session_id=None, max_rounds=opts.get("max_rounds"),
            )
        opened.append({"intake_id": claimed["id"], "topic_id": topic["id"]})

    logger.info("forum_intake_drained", opened=len(opened))
    return {"opened": opened, "count": len(opened)}


@router.delete("/intake/{intake_id}", status_code=204)
async def reject(intake_id: UUID, reason: Optional[str] = None,
                 x_com_key: Optional[str] = Header(default=None, alias="X-COM-Key"),
                 db: AsyncSession = Depends(get_db)):
    _require_intake_key(x_com_key)
    if not await reject_intake(db, intake_id, reason):
        raise HTTPException(status_code=404, detail="Intake item not found")


# ======================================================================================
# Convenience
# ======================================================================================


@router.get("/stats")
async def forum_stats(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func as sa_func
    from db.forum_models import TopicIntake

    total = int(await db.scalar(sa_func.count(Topic.id)) or 0)
    concluded = int(await db.scalar(
        sa_func.count(Topic.id).where(Topic.status == "concluded")) or 0)
    consensus = int(await db.scalar(
        sa_func.count(Topic.id).where(Topic.outcome == "consensus")) or 0)
    plurality = int(await db.scalar(
        sa_func.count(Topic.id).where(Topic.outcome == "plurality")) or 0)
    posts = int(await db.scalar(sa_func.count(Post.id)) or 0)
    queued = int(await db.scalar(
        sa_func.count(TopicIntake.id).where(TopicIntake.status == "queued")) or 0)

    return {
        "topics": total,
        "concluded": concluded,
        "consensus": consensus,
        "plurality": plurality,
        "posts": posts,
        "intake_queued": queued,
        "cast": registry.keys(),
    }