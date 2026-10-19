"""An API-submitted question must never be lost.

`claim_intake` commits `status = 'opened'` *before* the caller creates a topic
or schedules the run. Anything failing in between — topic creation, task
scheduling, a process restart — left the question marked `opened` with no topic
and nothing left to pick it up: gone permanently, and no longer visible in
`GET /intake?status=queued`.

`drain_intake` had the worse variant. Its `background_tasks` defaulted to
`None` and the code guarded on `is not None`, so a `None` drained the entire
queue into topics that would never run and reported success.
"""

import pytest

from api import forum as forum_api
from agents import forum_store


SESSION_LOOP = pytest.mark.asyncio(loop_scope="session")


# ======================================================================================
# The release primitive
# ======================================================================================


@pytest.mark.live
@SESSION_LOOP
async def test_release_puts_a_claimed_item_back():
    import uuid

    from db.postgres import async_session, init_db
    from agents.forum_store import claim_intake, enqueue_intake, release_intake

    await init_db()
    async with async_session() as db:
        entry = await enqueue_intake(db, "a question that must not vanish")
        iid = uuid.UUID(entry["id"])

        claimed = await claim_intake(db, iid)
        assert claimed is not None, "claim must succeed from queued"

        assert await release_intake(db, iid) is True

        # And it must be claimable again — that is the whole point.
        again = await claim_intake(db, iid)
        assert again is not None, (
            "a released item is not claimable again, so releasing it would just "
            "move the question out of the queue permanently"
        )


@pytest.mark.live
@SESSION_LOOP
async def test_release_requeues_an_item_that_has_a_topic():
    """A claimed item can already carry a topic when the *scheduling* fails.

    Releasing drops the link, so the item is re-claimable and a later drain
    opens a fresh topic rather than leaving the question attached to a thread
    that will never run.
    """
    import uuid

    from db.postgres import async_session, init_db
    from agents.forum_store import claim_intake, enqueue_intake, release_intake

    await init_db()
    async with async_session() as db:
        entry = await enqueue_intake(db, "claimed by someone else")
        iid = uuid.UUID(entry["id"])

        # Claim and *link* it, as a successful drain would.
        assert await claim_intake(db, iid) is not None
        topic = await forum_store.create_topic(
            db, "claimed by someone else", title="Owned", cast=["oracle"]
        )
        await forum_store.link_intake_to_topic(db, iid, uuid.UUID(topic["id"]))

        assert await release_intake(db, iid) is True  # our failure path fires

        # The stale link is dropped, so the question is not left attached to a
        # topic that will never run, and it is claimable again.
        from sqlalchemy import select
        from db.forum_models import TopicIntake

        row = (await db.execute(
            select(TopicIntake).where(TopicIntake.id == iid)
        )).scalar_one()
        assert row.status == "queued"
        assert row.topic_id is None, "the link to the dead topic was kept"
        assert row.opened_at is None

        again = await claim_intake(db, iid)
        assert again is not None, "the released question is no longer claimable"


@pytest.mark.live
@SESSION_LOOP
async def test_release_of_a_queued_item_is_a_no_op():
    """Never steal an item that was never claimed."""
    import uuid

    from db.postgres import async_session, init_db
    from agents.forum_store import enqueue_intake, release_intake

    await init_db()
    async with async_session() as db:
        entry = await enqueue_intake(db, "still waiting its turn")
        assert await release_intake(db, uuid.UUID(entry["id"])) is False


# ======================================================================================
# The callers must actually release
# ======================================================================================


class TestIntakePathsRequeue:
    def test_open_failure_releases_the_claim(self):
        from pathlib import Path

        src = Path("api/forum.py").read_text()
        fn = src.split("async def _open_intake_topic")[1].split("\nasync def ")[0]
        assert "except Exception" in fn, (
            "topic creation can fail after the claim is committed; without a "
            "handler the question is lost"
        )
        assert "_requeue_intake" in fn

    def test_schedule_failure_releases_the_claim(self):
        """A topic that exists but will never run is still a lost question."""
        from pathlib import Path

        src = Path("api/forum.py").read_text()
        # Both the single-intake and the drain path schedule.
        assert src.count("forum_intake_schedule_failed") == 2
        assert src.count("await _requeue_intake(claimed[\"id\"])") >= 1

    def test_both_intake_entry_points_use_the_shared_helper(self):
        """One implementation, so the fix cannot drift between them."""
        from pathlib import Path

        src = Path("api/forum.py").read_text()
        assert src.count("_open_intake_topic(") == 3, (
            "both intake handlers must route through _open_intake_topic "
            "(definition + two call sites)"
        )
        # No handler should call create_topic for intake directly any more.
        assert 'origin="api", mode=opts.get("mode", "auto"),\n            cast=cast' not in src

    def test_requeue_helper_never_raises(self):
        from pathlib import Path

        fn = Path("api/forum.py").read_text().split("async def _requeue_intake")[1]
        fn = fn.split("\nasync def ")[0]
        assert "except Exception" in fn, (
            "_requeue_intake runs inside an except block; raising here would "
            "replace the original error with a confusing one"
        )

    def test_requeue_uses_a_fresh_session(self):
        """The caller's session may be mid-transaction and broken."""
        from pathlib import Path

        fn = Path("api/forum.py").read_text().split("async def _requeue_intake")[1]
        fn = fn.split("\nasync def ")[0]
        assert "async_session()" in fn


class TestDrainRefusesWithoutARunner:
    def test_no_longer_creates_unrunnable_topics(self):
        from pathlib import Path

        src = Path("api/forum.py").read_text()
        fn = src.split("async def drain_intake")[1].split("\n@router")[0]
        assert "if background_tasks is None:" in fn
        assert "status_code=503" in fn, (
            "without a runner the questions must stay queued; creating topics "
            "that can never run is silent permanent loss"
        )
        assert "if background_tasks is not None:" not in fn, (
            "the old guard skipped scheduling and still reported success"
        )


# ======================================================================================
# Chroma: a missing collection is not the same as an unreachable one
# ======================================================================================


class TestChromaFailureModes:
    def test_missing_collection_is_distinguished(self):
        from db.chroma import _is_missing_collection

        assert _is_missing_collection(Exception("collection not found")) is True
        assert _is_missing_collection(Exception("does not exist")) is True

    def test_unreachable_is_not_treated_as_missing(self):
        from db.chroma import _is_missing_collection

        # The old code turned every one of these into `create_collection`.
        assert _is_missing_collection(Exception("Connection refused")) is False
        assert _is_missing_collection(Exception("401 Unauthorized")) is False
        assert _is_missing_collection(Exception("read timed out")) is False

    def test_get_collection_does_not_create_on_a_connection_error(self):
        """That path was the whole bug: two silent failures stacked."""
        from db.chroma import get_collection

        calls = []

        class _Client:
            def get_collection(self, name):
                calls.append(("get", name))
                raise RuntimeError("Connection refused")

            def create_collection(self, name):
                calls.append(("create", name))
                raise RuntimeError("Connection refused")

        import db.chroma as chroma
        original = chroma.get_client
        chroma.get_client = lambda: _Client()
        try:
            assert get_collection("probe") is None
        finally:
            chroma.get_client = original

        assert calls == [("get", "probe")], (
            f"create_collection must not be attempted on a connection error: {calls}"
        )


# ======================================================================================
# topic.cast must be read inside the session
# ======================================================================================


class TestDetachedAttributeAccess:
    def test_cast_read_inside_the_session_block(self):
        """`topic.cast` after the block only worked by `expire_on_commit=False`."""
        from pathlib import Path

        src = Path("api/forum_ws.py").read_text()
        fn = src.split("async def _handle_user_reply")[1].split("\nasync def ")[0]

        session_end = fn.index("await streams.publish(topic_id, {\"type\": \"post_created\"")
        before = fn[:session_end]
        # The cast must be captured inside the `async with` block.
        assert "cast = list(topic.cast or [])" in before
        assert "and topic.cast:" not in fn, (
            "using topic.cast after the session closes raises "
            "DetachedInstanceError if expire_on_commit is ever changed"
        )
        assert "and cast:" in fn


# ======================================================================================
# Regression: the API reports the requeue honestly
# ======================================================================================


@pytest.mark.live
@SESSION_LOOP
async def test_intake_round_trip_still_works():
    """The happy path must be untouched by the error handling."""
    import uuid

    from db.postgres import async_session, init_db
    from agents.forum_store import claim_intake, enqueue_intake, list_intake

    await init_db()
    async with async_session() as db:
        entry = await enqueue_intake(db, "a perfectly ordinary question")
        iid = uuid.UUID(entry["id"])

        queued = await list_intake(db, status="queued", limit=200)
        assert any(i["id"] == entry["id"] for i in queued)

        assert await claim_intake(db, iid) is not None
        queued_after = await list_intake(db, status="queued", limit=200)
        assert not any(i["id"] == entry["id"] for i in queued_after)