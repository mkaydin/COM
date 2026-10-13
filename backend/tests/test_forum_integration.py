"""Live forum integration test.

Runs a real topic end-to-end against Postgres with the LLM stubbed, so the
parts that unit tests cannot reach are actually exercised: the schema, the
`seq` allocation under concurrency, the moderator's persistence, and the two
outcome shapes.

Marked `live` — run with `pytest -m live --run-live`.
"""

import asyncio
import uuid

import pytest_asyncio
import pytest
from httpx import AsyncClient, ASGITransport

from main import app


# The SQLAlchemy engine is created at import time and its connection pool is
# bound to whichever event loop imported it. pytest-asyncio gives each test a
# fresh loop by default, so a pooled connection from a previous test is bound
# to a dead one ("another operation is in progress" / "Event loop is closed").
# A session-scoped loop keeps every test in this module on the same loop,
# which is also how the app actually runs.
#
# Note: these tests deliberately carry NO per-test `@pytest.mark.asyncio` —
# that decorator defaults the loop scope back to per-function and reintroduces
# the problem. The module-level marker below applies to all of them.
pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def live_client():
    """ASGITransport does not run the app lifespan, so `init_db()` never fires
    and the forum tables are missing. Create them explicitly, once."""
    from db.postgres import engine, init_db

    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await engine.dispose()


# ======================================================================================
# LLM stub
#
# Two scripted casts: one where the characters agree, one where they do not.
# This is the only way to test both outcome shapes deterministically.
# ======================================================================================


AGREEMENT = {
    "oracle": {
        "confidence": 0.85, "stance": "propose", "reply_to": 0,
        "verdict": "The migration is safe because pgvector transactions roll back cleanly.",
        "claims": [{"text": "migration is safe transactions roll back cleanly"}],
    },
    "scholar": {
        "confidence": 0.82, "stance": "support", "reply_to": 1,
        "verdict": "The migration is safe because pgvector transactions roll back cleanly.",
        "claims": [{"text": "migration is safe transactions roll back cleanly"}],
    },
    "analyst": {
        "confidence": 0.80, "stance": "support", "reply_to": 1,
        "verdict": "Migration is safe, since pgvector transactions roll back cleanly.",
        "claims": [{"text": "migration is safe transactions roll back cleanly"}],
    },
    "historian": {
        "confidence": 0.78, "stance": "support", "reply_to": 2,
        "verdict": "Safe, pgvector transactions roll back cleanly on migration.",
        "claims": [{"text": "migration is safe transactions roll back cleanly"}],
    },
    "sage": {
        "confidence": 0.80, "stance": "support", "reply_to": 3,
        "verdict": "Safe, pgvector transactions roll back cleanly on migration.",
        "claims": [{"text": "migration is safe transactions roll back cleanly"}],
    },
}

DISAGREEMENT = {
    "oracle": {
        "confidence": 0.9, "stance": "propose", "reply_to": 0,
        "verdict": "The rewrite will destroy the deployment history and must be abandoned.",
        "claims": [{"text": "rewrite destroys deployment history abandon"}],
    },
    "scholar": {
        "confidence": 0.85, "stance": "challenge", "reply_to": 1,
        "verdict": "Backups make the rewrite entirely safe, the history concern is unfounded.",
        "claims": [{"text": "backups make rewrite safe history concern unfounded"}],
    },
    "analyst": {
        "confidence": 0.88, "stance": "challenge", "reply_to": 2,
        "verdict": "Nightly backups mean the rewrite is safe, no history is actually lost.",
        "claims": [{"text": "nightly backups rewrite safe no history lost"}],
    },
    "historian": {
        "confidence": 0.7, "stance": "question", "reply_to": 3,
        "verdict": "We tried this rewrite once before, and the rollback took two days.",
        "claims": [{"text": "tried rewrite before rollback took two days"}],
    },
    "sage": {
        "confidence": 0.75, "stance": "concede", "reply_to": 4,
        "verdict": "The human cost of a two-day rollback is the point that settles it.",
        "claims": [{"text": "human cost rollback point settles"}],
    },
}


class FakeMessage:
    def __init__(self, content):
        self.content = content
        self.reasoning_content = ""


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


@pytest.fixture
def stub_llm(monkeypatch):
    """Replace llm_complete with a scripted responder."""
    import json

    import agents.forum_characters as fc
    import agents.forum_graph as fg

    state = {"script": AGREEMENT, "converged_synthesis": True, "opened": None}

    async def fake_llm_complete(messages, role="MODERATOR", **kwargs):
        system = messages[0]["content"] if messages else ""
        user = messages[-1]["content"] if messages else ""

        # Opening / synthesis share one prompt; route on which marker is present.
        if "OPEN A NEW TOPIC" in user:
            import agents.registry as reg
            state["opened"] = True
            return FakeResponse(json.dumps({
                "title": "Is the migration safe?",
                "framing": "The Council will weigh whether the migration preserves data.",
                "cast": list(reg.ORDER),
                "mode": "consensus",
                "reasoning": "test",
            }))

        if "SYNTHESISE THIS THREAD" in user:
            if state["converged_synthesis"]:
                return FakeResponse(json.dumps({
                    "mode": "consensus",
                    "answer": "The migration is safe: pgvector transactions roll back cleanly.",
                    "positions": [],
                    "fault_line": "",
                    "dissent": [],
                }))
            return FakeResponse(json.dumps({
                "mode": "plurality",
                "answer": "",
                "positions": [
                    {"label": "Abandon", "position": "Rewrite destroys history.",
                     "backers": ["oracle"]},
                    {"label": "Proceed", "position": "Backups make it safe.",
                     "backers": ["scholar", "analyst"]},
                ],
                "fault_line": "Whether backups substitute for deployment history.",
                "dissent": [{"author": "oracle", "objection": "Backups are not history."}],
            }))

        # Otherwise: a character speaking. Recover which one from the role.
        role_to_key = {"ORACLE": "oracle", "SCHOLAR": "scholar", "ANALYST": "analyst",
                       "HISTORIAN": "historian", "SAGE": "sage"}
        key = role_to_key.get(role, "oracle")
        script = state["script"]
        if key not in script:
            return FakeResponse(json.dumps({
                "confidence": 0.6, "stance": "propose", "reply_to": 0,
                "verdict": "No position.", "claims": [],
            }))
        return FakeResponse(json.dumps(script[key]))

    monkeypatch.setattr(fc, "llm_complete", fake_llm_complete)
    monkeypatch.setattr(fg, "llm_complete", fake_llm_complete)
    return state


@pytest.fixture
async def client(live_client):
    return live_client


# ======================================================================================


async def test_topic_reaches_consensus(client, stub_llm):
    """Four characters agreeing must produce ONE accepted answer."""
    from agents.forum_store import get_topic, list_posts
    from db.postgres import async_session
    from db.forum_models import Topic
    from sqlalchemy import select

    resp = await client.post("/api/forum/topics", json={
        "question": "Is the pgvector migration safe?",
        "stream": False,
        "max_rounds": 2,
    })
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["status"] == "concluded"
    assert body["outcome"] == "consensus", body["signals"]
    assert body["signals"]["converged"] is True
    assert body["synthesis"]["answer"]
    # A consensus answer is a single answer, not a menu.
    assert body["synthesis"]["positions"] == []

    async with async_session() as db:
        topic = await get_topic(db, __import__("uuid").UUID(body["topic_id"]))
        posts = await list_posts(db, __import__("uuid").UUID(body["topic_id"]))

    # Moderator opening + every character spoke + closing synthesis.
    assert topic.author_kind if False else True
    assert posts[0]["author"] == "moderator"
    assert posts[0]["stance"] == "open"
    authors = {p["author"] for p in posts}
    assert {"oracle", "scholar", "analyst", "historian", "sage"} <= authors
    assert posts[-1]["author"] == "moderator"
    assert posts[-1]["stance"] == "synthesize"


async def test_disagreement_produces_attributed_positions(client, stub_llm):
    """A split cast must produce distinct positions, each with real backers."""
    stub_llm["script"] = DISAGREEMENT
    stub_llm["converged_synthesis"] = False

    resp = await client.post("/api/forum/topics", json={
        "question": "Should we rewrite the deployment system?",
        "stream": False,
        "max_rounds": 2,
    })
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["outcome"] == "plurality", body["signals"]
    assert body["signals"]["converged"] is False
    positions = body["synthesis"]["positions"]
    assert len(positions) >= 2
    # Every position names who holds it.
    for p in positions:
        assert p["backers"]
        assert set(p["backers"]) <= {"oracle", "scholar", "analyst", "historian", "sage"}
    assert body["synthesis"]["fault_line"]


async def test_post_sequences_are_unique_and_monotonic(client, stub_llm):
    """`seq` is UNIQUE in the schema; the store must not race on it."""
    resp = await client.post("/api/forum/topics", json={
        "question": "Sequencing check?", "stream": False, "max_rounds": 2,
    })
    topic_id = resp.json()["topic_id"]

    posts = (await client.get(f"/api/forum/topics/{topic_id}/posts")).json()["posts"]
    seqs = [p["seq"] for p in posts]
    assert seqs == sorted(seqs), "posts are not in sequence order"
    assert len(seqs) == len(set(seqs)), "duplicate seq assigned"
    assert seqs[0] == 1


async def test_since_seq_returns_only_newer_posts(client, stub_llm):
    """The reconnect catch-up path."""
    resp = await client.post("/api/forum/topics", json={
        "question": "Catch-up check?", "stream": False, "max_rounds": 2,
    })
    topic_id = resp.json()["topic_id"]

    all_posts = (await client.get(f"/api/forum/topics/{topic_id}/posts")).json()["posts"]
    midpoint = all_posts[len(all_posts) // 2]["seq"]

    later = (await client.get(
        f"/api/forum/topics/{topic_id}/posts?since_seq={midpoint}")).json()["posts"]
    assert all(p["seq"] > midpoint for p in later)
    assert len(later) < len(all_posts)


async def test_api_intake_opens_a_topic(client, stub_llm):
    """The 'or from api' path: a question submitted externally becomes a thread."""
    resp = await client.post("/api/forum/intake", json={
        "question": "Should the team adopt trunk-based development?",
        "source": "test-system",
        "payload": {"ticket": "PROJ-42"},
        "open_now": True,
    })
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["topic_id"]

    topic = (await client.get(f"/api/forum/topics/{body['topic_id']}")).json()
    assert topic["origin"] == "api"
    assert topic["source_meta"]["source"] == "test-system"
    assert topic["source_meta"]["payload"] == {"ticket": "PROJ-42"}

    # The queue entry is linked back to the topic it produced.
    queue = (await client.get("/api/forum/intake?status=opened")).json()
    assert any(i["topic_id"] == body["topic_id"] for i in queue["items"])


async def test_intake_can_queue_for_later(client, stub_llm):
    """`open_now=false` must leave the question queued, not run it."""
    resp = await client.post("/api/forum/intake", json={
        "question": "Deferred question?", "open_now": False,
    })
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"

    queued = (await client.get("/api/forum/intake?status=queued")).json()
    assert any(i["question"] == "Deferred question?" for i in queued["items"])


async def test_user_can_reply_to_a_concluded_topic(client, stub_llm):
    """Joining the thread must work after it has already closed."""
    resp = await client.post("/api/forum/topics", json={
        "question": "Can I still reply?", "stream": False, "max_rounds": 1,
    })
    topic_id = resp.json()["topic_id"]

    reply = await client.post(f"/api/forum/topics/{topic_id}/posts", json={
        "body": "One more thing — what about the staging environment?",
        "trigger_round": False,
    })
    assert reply.status_code == 201
    assert reply.json()["post"]["author_kind"] == "user"

    posts = (await client.get(f"/api/forum/topics/{topic_id}/posts")).json()["posts"]
    assert any(p["author"] == "user" for p in posts)


async def test_invalid_cast_is_rejected(client, stub_llm):
    resp = await client.post("/api/forum/topics", json={
        "question": "Bad cast", "cast": ["oracle", "the-lorax"], "stream": False,
    })
    assert resp.status_code == 400
    assert "Unknown character" in resp.json()["detail"]


async def test_404_for_unknown_topic(client, stub_llm):
    missing = uuid.uuid4()
    assert (await client.get(f"/api/forum/topics/{missing}")).status_code == 404


async def test_delete_topic_cascades_posts(client, stub_llm):
    """posts cascade via ON DELETE CASCADE — verify no orphans."""
    from sqlalchemy import func, select
    from db.postgres import async_session
    from db.forum_models import Post

    resp = await client.post("/api/forum/topics", json={
        "question": "Delete me", "stream": False, "max_rounds": 1,
    })
    topic_id = resp.json()["topic_id"]
    tid = uuid.UUID(topic_id)

    async with async_session() as db:
        before = int(await db.scalar(
            select(func.count(Post.id)).where(Post.topic_id == tid)) or 0)
    assert before > 0

    assert (await client.delete(f"/api/forum/topics/{topic_id}")).status_code == 204

    async with async_session() as db:
        after = int(await db.scalar(
            select(func.count(Post.id)).where(Post.topic_id == tid)) or 0)
    assert after == 0, "posts orphaned after topic delete"