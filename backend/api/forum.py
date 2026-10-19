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
from sqlalchemy import select as sa_select, select
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
    """Run an already-created topic to its conclusion.

    The topic row is created before the run so a client can subscribe over
    WebSocket first; `topic_id` is passed into the graph so the Moderator's
    opening fills that row in instead of creating a second one.
    """
    from agents.forum_graph import run_topic

    return await run_topic(
        question,
        topic_id=topic_id,
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
    from llm import roles

    try:
        # No provider argument: each character resolves its own.
        bindings = {r["role"]: r for r in roles.all_roles()}
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

    # Resolve the round budget once, here. The graph falls back to
    # `settings.forum_max_rounds` when it is not told, so persisting a hardcoded
    # 2 would record a budget the run then ignores.
    from config import get_settings
    effective_rounds = body.max_rounds or get_settings().forum_max_rounds

    async with async_session() as sdb:
        topic = await create_topic(
            sdb,
            body.question,
            origin="user",
            mode=mode,
            cast=cast,
            tags=body.tags,
            session_id=body.session_id,
            max_rounds=effective_rounds,
        )

    logger.info("forum_topic_requested", topic_id=topic["id"], cast=cast, mode=mode)

    if body.stream:
        # Live path: the graph's per-post events are published to every socket
        # watching this topic, so the thread renders as it is written rather
        # than only after a client reconnects and replays it.
        from api.forum_ws import run_topic_streaming

        background_tasks.add_task(
            run_topic_streaming,
            topic["id"], body.question, cast=cast, mode=mode, tags=body.tags,
            origin="user", session_id=body.session_id,
            max_rounds=effective_rounds,
        )
        return {"topic_id": topic["id"], "status": "accepted", "mode": mode, "cast": cast}

    result = await _run_and_respond(
        topic["id"], body.question, cast=cast, mode=mode, tags=body.tags,
        origin="user", session_id=body.session_id, max_rounds=effective_rounds,
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
        if body.status not in ("open", "debating", "concluded", "archived", "failed"):
            raise HTTPException(status_code=400, detail="invalid status")
        topic.status = body.status
        # A manual status change is a recovery; drop the stale failure reason.
        if body.status != "failed":
            topic.error = None

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
        # A reply on a previously-failed topic is a retry; drop the stale reason.
        topic.error = None
        await db.commit()

    if body.trigger_round and topic.cast:
        background_tasks.add_task(_continue_thread_locked, str(topic_id))

    return {"post": post, "post_count": await count_posts(db, topic_id)}


async def _continue_thread(topic_id: str):
    """Run one more exchange round on a concluded/open thread.

    Each character sees the posts written earlier in this same round, so the
    cast replies into a live thread rather than in parallel. Synthesis is *not*
    run here — the caller decides whether to close (forum_graph's own loop
    ends at the round budget and then synthesises).
    """
    from agents.forum_characters import run_character
    from agents.forum_store import bump_round

    async with async_session() as db:
        topic = await get_topic(db, UUID(topic_id))
        if topic is None:
            return
        cast = topic.cast or []
        question = topic.question

    new_posts = []
    failed = []
    async with async_session() as db:
        for key in cast:
            so_far = await list_posts(db, UUID(topic_id))
            result = await run_character(key, question, posts=so_far, is_opening=False)
            # `run_character` reports a provider-level failure as a *truthy*
            # dict with ok=False (see forum_characters). Checking truthiness
            # alone would write an empty post per silent character, burying
            # the thread and poisoning the convergence signals with
            # zero-content clusters.
            if not result or not result.get("ok"):
                failed.append(key)
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
    if failed:
        logger.warning("forum_thread_partial_round", topic_id=topic_id, failed=failed)


async def _continue_thread_locked(topic_id: str):
    """Run `_continue_thread` under the per-topic lock.

    Every entry point that starts Council work must come through here: the REST
    reply, the reopen, and the WebSocket reply. Without it two replies in quick
    succession run two full rounds concurrently — interleaved posts, a double
    round bump, and two closing syntheses.

    The lock is NOT reentrant, so a caller that already holds it must call
    `_continue_thread` directly rather than this wrapper.
    """
    from api.forum_ws import streams  # local import: forum_ws imports this module

    async with streams.lock(topic_id):
        try:
            await _continue_thread(topic_id)
        except Exception as e:
            # Without this the topic stays `debating` forever: the callers set
            # that status before scheduling, and nothing was scheduled to change
            # it back.
            logger.error("forum_continuation_failed", topic_id=topic_id, error=str(e)[:200])
            await _mark_topic_failed(topic_id, e)


async def _mark_topic_failed(topic_id: str, exc: BaseException) -> None:
    """Record a dead run on the topic, in its own session.

    Uses a fresh session because the caller's may be mid-transaction and
    broken — reusing it would either roll back the failure record or raise a
    second error that masks the first.
    """
    from agents.forum_store import fail_topic

    try:
        async with async_session() as sdb:
            await fail_topic(sdb, topic_id, f"{type(exc).__name__}: {exc}")
    except Exception as e:  # pragma: no cover - defensive
        logger.error("forum_fail_record_failed", topic_id=topic_id, error=str(e)[:200])


@router.post("/topics/{topic_id}/close")
async def close_topic(topic_id: UUID, db: AsyncSession = Depends(get_db)):
    """Force a closing synthesis now, without waiting for the round budget.

    Idempotent. Re-closing a concluded topic returns the synthesis it already
    has; synthesising again would append a second closing post and rewrite the
    outcome from a thread that has since grown.
    """
    _forum_enabled()
    topic = await get_topic(db, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    posts = await list_posts(db, topic_id)

    if topic.status == "concluded" and topic.outcome:
        return {"topic_id": str(topic_id), "outcome": topic.outcome,
                "signals": topic.signals, "synthesis": topic.synthesis,
                "post_count": len(posts), "already_concluded": True}

    from agents.forum_graph import moderator_synthesis

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
            "post_count": len(posts), "already_concluded": False}


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
    background_tasks.add_task(_continue_thread_locked, str(topic_id))
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

    topic = await _open_intake_topic(db, claimed, cast, opts)
    if topic is None:
        return {"intake_id": claimed["id"], "status": "requeued",
                "hint": "Could not open a topic; the question is back in the queue."}

    # Same live-streaming path as a UI-opened topic, so an API-fed question
    # is watchable over the WebSocket while it runs.
    from api.forum_ws import run_topic_streaming

    try:
        background_tasks.add_task(
            run_topic_streaming, topic["id"], claimed["question"], cast=cast,
            mode=opts.get("mode", "auto"), tags=opts.get("tags") or [],
            origin="api", session_id=None, max_rounds=opts.get("max_rounds"),
        )
    except Exception as e:
        # The question is claimed and has a topic but nothing will run it.
        # Hand it back so a later drain picks it up.
        logger.error("forum_intake_schedule_failed",
                     intake_id=claimed["id"], topic_id=topic["id"], error=str(e)[:200])
        await _requeue_intake(claimed["id"])
        await _fail_orphan_topic(topic["id"], e)
        return {"intake_id": claimed["id"], "status": "requeued",
                "hint": "Could not schedule the run; the question is back in the queue."}

    return {"intake_id": claimed["id"], "topic_id": topic["id"], "status": "opening"}


async def _open_intake_topic(db, claimed, cast, opts):
    """Create the topic for a claimed intake item, or release it and return None.

    `claim_intake` has already committed `status='opened'` by this point, so
    every failure between here and the background task leaves the question
    stranded as `opened` with no topic. Returning None after requeueing is what
    keeps an API-submitted question from disappearing.
    """
    try:
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
        return topic
    except Exception as e:
        logger.error(
            "forum_intake_open_failed",
            intake_id=claimed.get("id"),
            error=str(e)[:200],
            hint="Releasing the intake item so the question is not lost.",
        )
        await _requeue_intake(claimed["id"])
        return None


async def _requeue_intake(intake_id: str) -> None:
    """Put a claimed intake item back in the queue. Never raises."""
    from agents.forum_store import release_intake

    try:
        async with async_session() as sdb:
            await release_intake(sdb, UUID(intake_id))
    except Exception as e:  # pragma: no cover - defensive
        logger.error("forum_intake_release_failed",
                     intake_id=intake_id, error=str(e)[:200])


async def _fail_orphan_topic(topic_id: str, exc: BaseException) -> None:
    """A topic that exists but will never run must not look runnable."""
    try:
        await _mark_topic_failed(topic_id, exc)
    except Exception as e:  # pragma: no cover - defensive
        logger.error("forum_orphan_mark_failed",
                     topic_id=topic_id, error=str(e)[:200])


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

    # FastAPI always injects BackgroundTasks, but the old `is not None` guard
    # meant that if it ever were None this endpoint would claim every question,
    # create a topic for each, and schedule nothing — the queue drains into
    # topics that sit `open` forever with no run and no error. Refusing is the
    # only safe reading: the questions stay queued.
    if background_tasks is None:
        raise HTTPException(
            status_code=503,
            detail="Draining requires a background task runner; nothing was claimed.",
        )

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

        topic = await _open_intake_topic(db, claimed, cast, opts)
        if topic is None:
            continue

        from api.forum_ws import run_topic_streaming

        try:
            background_tasks.add_task(
                run_topic_streaming, topic["id"], claimed["question"], cast=cast,
                mode=opts.get("mode", "auto"), tags=opts.get("tags") or [],
                origin="api", session_id=None, max_rounds=opts.get("max_rounds"),
            )
        except Exception as e:
            logger.error("forum_intake_schedule_failed",
                         intake_id=claimed["id"], topic_id=topic["id"], error=str(e)[:200])
            await _requeue_intake(claimed["id"])
            await _fail_orphan_topic(topic["id"], e)
            continue

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

    async def _count(model, *where):
        # `count(...).where(...)` is not valid SQLAlchemy — the filter has to
        # go through a select().
        return int(await db.scalar(
            sa_select(sa_func.count(model.id)).where(*where)
        ) or 0)

    topics = await _count(Topic)
    concluded = await _count(Topic, Topic.status == "concluded")
    consensus = await _count(Topic, Topic.outcome == "consensus")
    plurality = await _count(Topic, Topic.outcome == "plurality")
    posts = await _count(Post)
    queued = await _count(TopicIntake, TopicIntake.status == "queued")

    return {
        "topics": topics,
        "concluded": concluded,
        "consensus": consensus,
        "plurality": plurality,
        "posts": posts,
        "intake_queued": queued,
        "cast": registry.keys(),
    }