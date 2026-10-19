"""A dead run must not leave a topic stranded.

`status = "debating"` was set by the caller *before* the background task was
scheduled, and nothing was scheduled to change it back. A run that raised —
a provider outage, a crash, a cancelled task — therefore left the thread in
`debating` permanently: no conclusion, no synthesis, nothing retryable from the
UI, and `POST /close` on it synthesising from a half-written round.

These pin that a failure is recorded, that the reason is retrievable, and that
a successful retry clears it.
"""

import pytest

from api import forum as forum_api
from agents import forum_store


# ======================================================================================
# Pure helpers — no database needed
# ======================================================================================


class TestFailedStatusIsAllowed:
    def test_patch_accepts_failed(self):
        """`failed` is a real state now; rejecting it strands the topic harder."""
        from pathlib import Path

        src = Path("api/forum.py").read_text()
        assert '"failed"' in src
        assert '("open", "debating", "concluded", "archived")' not in src, (
            "the allowed status list must include `failed`, or a failed topic "
            "cannot be reopened through PATCH"
        )

    def test_topic_dict_exposes_the_error(self):
        from pathlib import Path

        src = Path("agents/forum_store.py").read_text()
        assert '"error"' in src, (
            "the UI cannot explain a failed topic without the reason on it"
        )


class TestTopicErrorIsClearedOnRetry:
    def test_reply_clears_a_stale_error(self):
        from pathlib import Path

        src = Path("api/forum.py").read_text()
        # Both the reply path and the generic status patch.
        assert src.count("topic.error = None") >= 2, (
            "a retry must clear the previous failure reason, or the UI shows "
            "an old error next to a topic that is now running"
        )

    def test_patch_clears_the_error_when_not_failed(self):
        from pathlib import Path

        src = Path("api/forum.py").read_text()
        assert 'if body.status != "failed":' in src


# ======================================================================================
# The wiring: every run path marks a dead run
# ======================================================================================


class TestEveryRunPathMarksFailure:
    def test_continuation_wrapper_catches(self):
        """The REST reply, the reopen and the WS reply all go through this."""
        from pathlib import Path

        src = Path("api/forum.py").read_text()
        fn = src.split("async def _continue_thread_locked")[1].split("\n@router")[0]
        assert "except Exception" in fn, (
            "_continue_thread_locked must catch; otherwise every caller that "
            "set status='debating' can strand a topic"
        )
        assert "_mark_topic_failed" in fn

    def test_streaming_run_catches(self):
        """The UI and the intake endpoint both use the streaming path."""
        from pathlib import Path

        src = Path("api/forum_ws.py").read_text()
        fn = src.split("async def run_topic_streaming")[1]
        assert "_mark_topic_failed" in fn
        assert '"type": "run_failed"' in fn, (
            "a client watching the socket should be told the run died, not "
            "just see the stream go quiet"
        )

    def test_marker_uses_its_own_session(self):
        """The caller's session may be mid-transaction and broken."""
        from pathlib import Path

        fn = Path("api/forum.py").read_text().split("async def _mark_topic_failed")[1]
        assert "async_session()" in fn
        assert "fail_topic" in fn
        # And it must never raise: this runs inside another except block.
        assert "except Exception" in fn


# ======================================================================================
# Against the database
# ======================================================================================


def _run(coro):
    import asyncio
    return asyncio.run(coro)


@pytest.fixture
def engine_url():
    """The async URL — every engine here is `create_async_engine`.

    Stripping `+asyncpg` (as the sync-engine tests in this suite do) loads
    psycopg2, which the asyncio extension rejects.
    """
    from config import get_settings
    return get_settings().postgres_url


# These three talk to the database through the shared `db.postgres.engine`
# (via `init_db`), whose connection pool is bound to whichever event loop first
# used it. Running them under `asyncio.run()` would bind that pool to a loop
# that is then closed, poisoning every later live test with "attached to a
# different loop" — which is exactly how the first version of this file broke
# the suite. The session-scoped loop below keeps them on the same loop as
# `test_forum_integration.py`.
SESSION_LOOP = pytest.mark.asyncio(loop_scope="session")


async def _ready():
    from db.postgres import init_db
    await init_db()


@pytest.mark.live
@SESSION_LOOP
async def test_fail_topic_records_the_reason():
    """The whole point: `failed` plus *why*, retrievable."""
    from db.postgres import async_session
    from agents.forum_store import create_topic, fail_topic, get_topic
    import uuid

    await _ready()
    async with async_session() as db:
        topic = await create_topic(
            db, "a question that will fail", title="Doomed", cast=["oracle"]
        )
        tid = uuid.UUID(topic["id"])

        # Pretend a run started and died.
        t = await get_topic(db, tid)
        t.status = "debating"
        await db.commit()

        await fail_topic(db, tid, "ProviderError: no API key")

        t = await get_topic(db, tid)
        assert t.status == "failed", "a dead run must not stay 'debating'"
        assert t.error and "no API key" in t.error

        as_dict = forum_store.topic_to_dict(t)
        assert as_dict["error"] and "no API key" in as_dict["error"]


@pytest.mark.live
@SESSION_LOOP
async def test_fail_topic_on_a_missing_topic_is_not_fatal():
    """It runs inside an error path; it must not raise a second error."""
    from db.postgres import async_session
    from agents.forum_store import fail_topic
    import uuid

    async with async_session() as db:
        assert await fail_topic(db, uuid.uuid4(), "boom") is None


@pytest.mark.live
@SESSION_LOOP
async def test_a_failed_topic_can_be_recovered():
    """The recovery path: reopen it and the stale reason is dropped."""
    from db.postgres import async_session
    from agents.forum_store import (
        create_topic, fail_topic, get_topic, clear_topic_error
    )
    import uuid

    await _ready()
    async with async_session() as db:
        topic = await create_topic(db, "recoverable?", title="Recovery")
        tid = uuid.UUID(topic["id"])
        await fail_topic(db, tid, "transient outage")

        t = await get_topic(db, tid)
        assert t.status == "failed"

        await clear_topic_error(db, tid)
        t.status = "open"
        await db.commit()

        t = await get_topic(db, tid)
        assert t.status == "open"
        assert t.error is None, "the stale failure reason survived the retry"