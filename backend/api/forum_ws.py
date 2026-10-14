"""Live forum threads over WebSocket.

The old `/ws/sessions/{id}/stream` streams a one-shot deliberation: query in,
three agent cards out. A forum thread is different — it unfolds over time, and
the interesting part is watching characters answer each other.

So this is a subscription endpoint. Connect to a topic, receive its posts as
they are written, and optionally post as the user mid-thread.

Message flow:

    → {"type":"subscribe"}
    ← topic_opened          the topic + its opening post
    ← post_created          one per post, as written (streamed from the run)
    ← round_started         a new exchange round begins
    ← signals_updated       deterministic convergence after each round
    ← synthesis             the outcome: consensus answer or plural positions
    → {"type":"reply","body":"..."}   the user joins
    ← error                 something went wrong
"""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from logging_config import get_logger

router = APIRouter(tags=["forum-ws"])
logger = get_logger(__name__)


class TopicStream:
    """Fan-out of graph events to every socket watching a topic.

    One run, many watchers. Without this, a topic opened by the UI would need
    a second run to be visible to a second tab.
    """

    def __init__(self):
        self._subscribers: dict[str, list[WebSocket]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def subscribe(self, topic_id: str, ws: WebSocket) -> None:
        self._subscribers.setdefault(topic_id, []).append(ws)

    def unsubscribe(self, topic_id: str, ws: WebSocket) -> None:
        watchers = self._subscribers.get(topic_id)
        if not watchers:
            return
        if ws in watchers:
            watchers.remove(ws)
        if not watchers:
            self._subscribers.pop(topic_id, None)

    def watcher_count(self, topic_id: str) -> int:
        return len(self._subscribers.get(topic_id, []))

    def lock(self, topic_id: str) -> asyncio.Lock:
        """One run per topic at a time — a double-click must not double-post."""
        return self._locks.setdefault(topic_id, asyncio.Lock())

    async def publish(self, topic_id: str, message: dict) -> None:
        watchers = list(self._subscribers.get(topic_id, []))
        if not watchers:
            return
        payload = json.dumps(message, default=str)
        dead = []
        for ws in watchers:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unsubscribe(topic_id, ws)


streams = TopicStream()


async def _replay_existing(topic_id: str, ws: WebSocket, since_seq: Optional[int]) -> None:
    """Send the thread so far, so a late or reconnecting client is whole."""
    from uuid import UUID

    from db.postgres import async_session
    from db.forum_models import Topic
    from agents.forum_store import get_topic, list_posts, topic_to_dict

    try:
        async with async_session() as db:
            topic = await get_topic(db, UUID(topic_id))
            if topic is None:
                await ws.send_text(json.dumps({
                    "type": "error", "message": f"Topic {topic_id} not found"}))
                return
            posts = await list_posts(db, UUID(topic_id), since_seq=since_seq)
            payload = topic_to_dict(topic)

        await ws.send_text(json.dumps({"type": "topic_opened", "topic": payload}))
        for p in posts:
            await ws.send_text(json.dumps({"type": "post_created", "post": p}))
        if payload.get("outcome"):
            await ws.send_text(json.dumps({
                "type": "synthesis",
                "outcome": payload["outcome"],
                "synthesis": payload.get("synthesis"),
                "signals": payload.get("signals"),
            }))
    except Exception as e:
        logger.error("forum_ws_replay_failed", topic_id=topic_id, error=str(e)[:160])
        await ws.send_text(json.dumps({"type": "error", "message": str(e)[:200]}))


@router.websocket("/ws/forum/{topic_id}")
async def forum_stream(websocket: WebSocket, topic_id: str,
                       since_seq: Optional[int] = Query(default=None)):
    """Watch a topic unfold; optionally post as the user."""
    await websocket.accept()
    logger.info("forum_ws_connected", topic_id=topic_id)

    streams.subscribe(topic_id, websocket)
    await _replay_existing(topic_id, websocket, since_seq)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps(
                    {"type": "error", "message": "invalid JSON"}))
                continue

            kind = msg.get("type")

            if kind == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

            elif kind == "reply":
                await _handle_user_reply(topic_id, websocket, msg)

            elif kind == "close":
                await _handle_close(topic_id, websocket)

    except WebSocketDisconnect:
        logger.info("forum_ws_disconnected", topic_id=topic_id)
    except Exception as e:
        logger.error("forum_ws_error", topic_id=topic_id, error=str(e)[:160])
    finally:
        streams.unsubscribe(topic_id, websocket)


async def _handle_user_reply(topic_id: str, ws: WebSocket, msg: dict) -> None:
    from uuid import UUID

    from db.postgres import async_session
    from db.forum_models import Topic
    from agents.forum_store import append_post, get_topic

    body = (msg.get("body") or "").strip()
    if not body:
        await ws.send_text(json.dumps({"type": "error", "message": "empty reply"}))
        return

    try:
        async with async_session() as db:
            topic = await get_topic(db, UUID(topic_id))
            if topic is None:
                await ws.send_text(json.dumps(
                    {"type": "error", "message": "topic not found"}))
                return
            post = await append_post(
                db, UUID(topic_id), "user", body, author_kind="user",
                stance="question", round=topic.round_count or 0,
            )
            if topic.status == "open":
                topic.status = "debating"
                await db.commit()

        # Broadcast to every watcher, then let the Council respond.
        await streams.publish(topic_id, {"type": "post_created", "post": post})

        if msg.get("trigger_round", True) and topic.cast:
            asyncio.create_task(_run_continuation(topic_id))

    except Exception as e:
        logger.error("forum_ws_reply_failed", topic_id=topic_id, error=str(e)[:160])
        await ws.send_text(json.dumps({"type": "error", "message": str(e)[:200]}))


async def _run_continuation(topic_id: str) -> None:
    """One more exchange round after a user reply, streamed as it goes."""
    # `_continue_thread_locked` takes the per-topic lock itself. It must not be
    # taken here as well — asyncio.Lock is not reentrant, so that deadlocks.
    from api.forum import _continue_thread_locked

    await _continue_thread_locked(topic_id)


async def _handle_close(topic_id: str, ws: WebSocket) -> None:
    try:
        async with streams.lock(topic_id):
            await _close_locked(topic_id, ws)
    except Exception as e:
        logger.error("forum_ws_close_failed", topic_id=topic_id, error=str(e)[:160])
        await ws.send_text(json.dumps({"type": "error", "message": str(e)[:200]}))


async def _close_locked(topic_id: str, ws: WebSocket) -> None:
    """Run the closing synthesis. Caller must hold the per-topic lock.

    Closing synthesises, so it races against a round in flight exactly the way
    two replies do — hence the same lock as `_run_continuation`.
    """
    from uuid import UUID

    from db.postgres import async_session
    from agents.forum_store import get_topic

    async with async_session() as db:
        topic = await get_topic(db, UUID(topic_id))
        if topic is None:
            await ws.send_text(json.dumps(
                {"type": "error", "message": "topic not found"}))
            return
        question, title = topic.question, topic.title
        round_no = topic.round_count or 0

    from agents.forum_graph import moderator_synthesis

    await streams.publish(topic_id, {"type": "round_started", "round": round_no + 1})
    result = await moderator_synthesis({
        "topic_id": topic_id,
        "question": question,
        "title": title,
        "round": round_no,
        "max_rounds": round_no + 1,
        "forced": True,
    })
    await streams.publish(topic_id, {
        "type": "synthesis",
        "outcome": result.get("outcome"),
        "synthesis": result.get("synthesis"),
        "signals": result.get("signals"),
    })
    await streams.publish(topic_id, {
        "type": "topic_concluded",
        "topic_id": topic_id,
        "outcome": result.get("outcome"),
    })


# ======================================================================================
# Opening a topic *with* live streaming
# ======================================================================================


async def run_topic_streaming(topic_id: str, question: str, *, cast=None, mode="auto",
                              tags=None, origin="user", session_id=None,
                              max_rounds=None) -> None:
    """Run a topic, publishing every post as the graph produces it.

    Used by the intake path and by anything that wants the outcome without a
    blocking HTTP request. `run_topic`'s `on_event` hook is what makes the
    thread visible while it is still being written.
    """
    from agents.forum_graph import run_topic

    async def on_event(kind: str, payload: dict):
        if kind == "open_topic":
            await streams.publish(topic_id, {
                "type": "topic_opened",
                "topic": {
                    "id": topic_id,
                    "title": payload.get("title"),
                    "framing": payload.get("framing"),
                    "cast": payload.get("cast"),
                    "mode": payload.get("mode"),
                },
            })
            opening = next((p for p in payload.get("posts", [])), None)
            if opening:
                await streams.publish(topic_id, {"type": "post_created", "post": opening})

        elif kind in ("statements", "exchange"):
            round_no = payload.get("round", 0)
            await streams.publish(topic_id, {
                "type": "round_started",
                "round": round_no,
                "participants": cast or [],
            })
            for p in payload.get("posts", []):
                await streams.publish(topic_id, {"type": "post_created", "post": p})
            await streams.publish(topic_id, {
                "type": "round_completed",
                "round": round_no,
                "posted": len(payload.get("posts", [])),
            })

        elif kind == "synthesis":
            await streams.publish(topic_id, {
                "type": "signals_updated",
                "signals": payload.get("signals"),
            })
            await streams.publish(topic_id, {
                "type": "synthesis",
                "outcome": payload.get("outcome"),
                "synthesis": payload.get("synthesis"),
                "signals": payload.get("signals"),
            })
            await streams.publish(topic_id, {
                "type": "topic_concluded",
                "topic_id": topic_id,
                "outcome": payload.get("outcome"),
            })

    lock = streams.lock(topic_id)
    async with lock:
        try:
            await run_topic(
                question, topic_id=topic_id, cast=cast, mode=mode, tags=tags,
                origin=origin, session_id=session_id, max_rounds=max_rounds,
                on_event=on_event,
            )
        except Exception as e:
            logger.error("forum_stream_run_failed", topic_id=topic_id, error=str(e)[:160])
            await streams.publish(topic_id, {"type": "error", "message": str(e)[:200]})