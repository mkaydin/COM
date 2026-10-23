"""Regression tests for the forum API's orchestration contracts.

`api/forum.py` and `api/forum_ws.py` had no unit coverage at all, which is how
these four defects shipped together:

1. `run_topic_streaming` did not pass `topic_id` to `run_topic`, so every
   streamed topic created a *second*, orphan topic row. The posts, the
   synthesis and `conclude_topic` all landed on the orphan; the topic id the
   API returned to the client stayed empty forever. The non-streaming path was
   correct, and it is the one the integration tests exercise — so they passed.

2. `_continue_thread` tested `if not result:` where `run_character` signals a
   provider failure with a *truthy* dict carrying `ok=False`. A silent cast
   therefore wrote one empty post per character and poisoned the convergence
   signals with zero-content clusters.

3. Only the WebSocket reply path took the per-topic lock. The REST reply, the
   reopen and the WebSocket close all bypassed it, so two of them could run
   full rounds concurrently on one topic.

4. `catalogue.free_models_for` matched only `"opencode"`, so under the shipped
   default (`LLM_PROVIDER=opencode-cli`) the setup UI listed no free models at
   all.

These are pure orchestration assertions — no Postgres, no LLM.
"""

import asyncio

import pytest

from agents import forum_characters, forum_graph, forum_store, registry
from api import forum as forum_api
from api import forum_ws
from llm import catalogue


# ======================================================================================
# Helpers — a minimal stand-in for the async session the API uses.
# ======================================================================================


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class _FakeSession:
    """`async with async_session() as db:` — hands out one reusable fake db."""

    def __init__(self, db=None):
        self.db = db or _FakeDB()

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *exc):
        return False


class _FakeTopic:
    def __init__(self, cast, question="Should we adopt trunk-based development?"):
        self.cast = cast
        self.question = question
        self.round_count = 0


# ======================================================================================
# 1. The streaming path must reuse the topic the caller already created.
# ======================================================================================


class TestStreamingReusesCallerTopic:
    """A streamed topic must land on the id the client was handed.

    This is the regression guard for the orphan-topic bug. The assertion is on
    the *call*, not the database: `run_topic` receives `topic_id` and therefore
    takes its "fill in the existing row" branch in `open_topic`.
    """

    @pytest.mark.asyncio
    async def test_run_topic_receives_the_topic_id(self, monkeypatch):
        captured = {}

        async def fake_run_topic(question, **kwargs):
            captured.update(kwargs)
            return {}

        monkeypatch.setattr(forum_graph, "run_topic", fake_run_topic)

        topic_id = "11111111-2222-3333-4444-555555555555"
        await forum_ws.run_topic_streaming(
            topic_id, "a question", cast=["oracle"], max_rounds=2
        )

        assert "topic_id" in captured, (
            "run_topic_streaming must forward topic_id; without it open_topic "
            "creates a second, orphan topic and the client's topic stays empty"
        )
        assert captured["topic_id"] == topic_id

    @pytest.mark.asyncio
    async def test_other_arguments_still_forwarded(self, monkeypatch):
        """The fix must not drop the rest of the signature."""
        captured = {}

        async def fake_run_topic(question, **kwargs):
            captured.update(kwargs)
            return {}

        monkeypatch.setattr(forum_graph, "run_topic", fake_run_topic)

        await forum_ws.run_topic_streaming(
            "22222222-2222-3333-4444-555555555555", "q",
            cast=["oracle", "sage"], mode="diverse", tags=["x"],
            origin="intake", session_id="s1", max_rounds=3,
        )

        assert captured["cast"] == ["oracle", "sage"]
        assert captured["mode"] == "diverse"
        assert captured["tags"] == ["x"]
        assert captured["origin"] == "intake"
        assert captured["session_id"] == "s1"
        assert captured["max_rounds"] == 3

    @pytest.mark.asyncio
    async def test_stream_failure_does_not_raise(self, monkeypatch):
        """A failed run is logged, not propagated into the background task."""

        async def boom(question, **kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr(forum_graph, "run_topic", boom)
        # Must not raise.
        await forum_ws.run_topic_streaming(
            "33333333-2222-3333-4444-555555555555", "q"
        )


# ======================================================================================
# 2. A silent character must not become an empty post.
# ======================================================================================


class _PatchContinue:
    """Stub out everything `_continue_thread` touches, keeping its real logic."""

    def __init__(self, monkeypatch, cast, results):
        self.appended = []
        self.bumped = 0
        db = _FakeDB()
        monkeypatch.setattr(forum_api, "async_session", lambda: _FakeSession(db))
        monkeypatch.setattr(forum_api, "get_topic",
                            _async_return(_FakeTopic(cast)))
        monkeypatch.setattr(forum_api, "list_posts", _async_return([]))
        monkeypatch.setattr(forum_store, "bump_round", self._bump)

        async def fake_run_character(key, question, posts=None, mode="exchange"):
            return results[key]

        monkeypatch.setattr(forum_characters, "run_character", fake_run_character)

    async def _bump(self, db, topic_id, commit=True):
        self.bumped += 1

    def append_post_stub(self, monkeypatch):
        async def fake_append_post(db, topic_id, author, body, **kwargs):
            self.appended.append((author, body, kwargs.get("author_kind")))
            return {"id": "p1", "seq": len(self.appended)}

        monkeypatch.setattr(forum_api, "append_post", fake_append_post)


def _async_return(value):
    async def _f(*args, **kwargs):
        return value

    return _f


def _ok(body="a considered opinion"):
    return {
        "author": "oracle", "stance": "propose", "body": body,
        "confidence": 0.8, "claims": [], "citations": [], "ok": True,
    }


def _silent():
    """Exactly what `run_character` returns on a provider-level failure."""
    return {
        "author": "oracle", "stance": "propose", "body": "",
        "confidence": 0.0, "claims": [], "citations": [],
        "extra": {"error": "no api key"}, "ok": False,
    }


class TestContinueThreadSkipsSilentCharacters:
    @pytest.mark.asyncio
    async def test_ok_false_writes_no_post(self, monkeypatch):
        patch = _PatchContinue(monkeypatch, ["oracle"], {"oracle": _silent()})
        patch.append_post_stub(monkeypatch)

        await forum_api._continue_thread(
            "44444444-2222-3333-4444-555555555555"
        )

        assert patch.appended == [], (
            "a character returning ok=False must not be written as an empty "
            "post — that is what the run_character contract documents"
        )

    @pytest.mark.asyncio
    async def test_silent_cast_still_bumps_the_round(self, monkeypatch):
        """The round advances; only the posts are withheld."""
        patch = _PatchContinue(monkeypatch, ["oracle"], {"oracle": _silent()})
        patch.append_post_stub(monkeypatch)

        await forum_api._continue_thread(
            "55555555-2222-3333-4444-555555555555"
        )

        assert patch.bumped == 1
        assert patch.appended == []

    @pytest.mark.asyncio
    async def test_healthy_character_still_posts(self, monkeypatch):
        """The fix must not silence the whole cast."""
        patch = _PatchContinue(
            monkeypatch, ["oracle"], {"oracle": _ok("here is my answer")}
        )
        patch.append_post_stub(monkeypatch)

        await forum_api._continue_thread(
            "66666666-2222-3333-4444-555555555555"
        )

        assert patch.appended == [("oracle", "here is my answer", "character")]

    @pytest.mark.asyncio
    async def test_partial_cast_writes_only_the_live_one(self, monkeypatch):
        patch = _PatchContinue(
            monkeypatch, ["oracle", "sage"], {"oracle": _ok(), "sage": _silent()}
        )
        patch.append_post_stub(monkeypatch)

        await forum_api._continue_thread(
            "77777777-2222-3333-4444-555555555555"
        )

        assert [a for a, _, _ in patch.appended] == ["oracle"]


# ======================================================================================
# 3. One run per topic, whichever door the work comes through.
# ======================================================================================


class TestPerTopicLock:
    @pytest.mark.asyncio
    async def test_continue_thread_locked_takes_the_lock(self, monkeypatch):
        topic_id = "88888888-2222-3333-4444-555555555555"
        held_during = []

        async def fake_continue(tid):
            held_during.append(
                forum_ws.streams.lock(tid).locked()
            )

        monkeypatch.setattr(forum_api, "_continue_thread", fake_continue)
        await forum_api._continue_thread_locked(topic_id)

        assert held_during == [True], (
            "_continue_thread_locked must run the round while holding the "
            "per-topic lock"
        )

    @pytest.mark.asyncio
    async def test_ws_continuation_does_not_deadlock(self, monkeypatch):
        """`_run_continuation` must not re-take a lock it delegates.

        `asyncio.Lock` is not reentrant. If `_run_continuation` locked and the
        wrapper locked again, this would hang forever — so the test is bounded
        by a timeout.
        """
        topic_id = "99999999-2222-3333-4444-555555555555"

        async def fake_continue(tid):
            return None

        monkeypatch.setattr(forum_api, "_continue_thread", fake_continue)

        await asyncio.wait_for(
            forum_ws._run_continuation(topic_id), timeout=5
        )

    @pytest.mark.asyncio
    async def test_ws_continuation_actually_locks(self, monkeypatch):
        topic_id = "aaaaaaaa-2222-3333-4444-555555555555"
        held = []

        async def fake_continue(tid):
            held.append(forum_ws.streams.lock(tid).locked())

        monkeypatch.setattr(forum_api, "_continue_thread", fake_continue)
        await asyncio.wait_for(
            forum_ws._run_continuation(topic_id), timeout=5
        )

        assert held == [True]

    @pytest.mark.asyncio
    async def test_concurrent_rounds_are_serialised(self, monkeypatch):
        """Two continuations on one topic must not overlap."""
        topic_id = "bbbbbbbb-2222-3333-4444-555555555555"
        order = []

        async def fake_continue(tid):
            order.append("enter")
            await asyncio.sleep(0)
            order.append("exit")

        monkeypatch.setattr(forum_api, "_continue_thread", fake_continue)

        await asyncio.gather(
            forum_api._continue_thread_locked(topic_id),
            forum_api._continue_thread_locked(topic_id),
        )

        # Serialised means enter/exit pairs, never enter/enter.
        assert order == ["enter", "exit", "enter", "exit"]

    @pytest.mark.asyncio
    async def test_ws_close_takes_the_lock(self, monkeypatch):
        topic_id = "cccccccc-2222-3333-4444-555555555555"
        held = []

        async def fake_close(tid, ws):
            held.append(forum_ws.streams.lock(tid).locked())

        monkeypatch.setattr(forum_ws, "_close_locked", fake_close)
        await asyncio.wait_for(
            forum_ws._handle_close(topic_id, ws=_FakeWS()), timeout=5
        )

        assert held == [True], (
            "closing synthesises, so it races a round in flight exactly the "
            "way two replies do"
        )

    @pytest.mark.asyncio
    async def test_rest_entry_points_route_through_the_lock(self, monkeypatch):
        """post_reply and reopen must schedule the *locked* variant.

        Read from the source rather than executed: both register a BackgroundTask,
        and the contract is which callable they hand over.
        """
        src = _source_of(forum_api)
        assert src.count("background_tasks.add_task(_continue_thread_locked") == 2, (
            "both the REST reply and the reopen must schedule "
            "_continue_thread_locked"
        )
        assert "add_task(_continue_thread," not in src, (
            "a raw _continue_thread would run without the per-topic lock"
        )


class _FakeWS:
    async def send_text(self, text):
        return None


def _source_of(module):
    import inspect
    return inspect.getsource(module)


# ======================================================================================
# 4. Declared knowledge sources must actually be gathered.
# ======================================================================================


class TestKnowledgeSourcesAreReal:
    """A source name that no branch handles is silently dropped.

    The Oracle declared `"rag_chunks"`, which `gather_context` never matched, so
    it lost raw retrieval in the forum path while its own prompt promised it.
    """

    HANDLED = {"okf", "rag", "documents", "wiki", "graph", "sessions"}

    def test_every_character_source_is_handled(self):
        unhandled = {
            (c.key, source)
            for c in registry.BY_KEY.values()
            for source in c.knowledge
            if source not in self.HANDLED
        }
        assert not unhandled, (
            f"knowledge sources with no branch in gather_context: {unhandled}"
        )

    def test_oracle_gets_raw_retrieval(self):
        oracle = registry.BY_KEY["oracle"]
        assert "rag" in oracle.knowledge, (
            "the Oracle's prompt promises raw chunks; without a 'rag' source it "
            "silently loses them on the forum path"
        )

    @pytest.mark.asyncio
    async def test_unknown_source_is_reported_not_ignored(self):
        """An unhandled source must warn rather than vanish."""
        from structlog.testing import capture_logs

        broken = registry.Character(
            key="tester", display_name="Tester", archetype="probe",
            color="runed-gold", icon="auto_awesome", model_role="ORACLE",
            knowledge=("okf", "not_a_real_source"),
        )

        with capture_logs() as logs:
            await forum_characters.gather_context(broken, "q")

        assert any(
            entry.get("event") == "forum_unknown_knowledge_source"
            and entry.get("source") == "not_a_real_source"
            for entry in logs
        ), f"an unknown knowledge source must be logged, not skipped: {logs}"


# ======================================================================================
# 5. The default provider must list its free models.
# ======================================================================================


class TestFreeModelCatalogue:
    def test_default_provider_lists_free_models(self):
        """`opencode-cli` is the shipped default and the way you get them free."""
        models = catalogue.free_models_for("opencode-cli")
        assert models, (
            "the setup UI showed no free models under the default provider; "
            "the CLI is precisely how you reach them"
        )
        assert all(m["free"] for m in models)

    def test_http_transport_shares_the_catalogue(self):
        assert (catalogue.free_models_for("opencode-cli")
                == catalogue.free_models_for("opencode"))

    def test_ollama_has_no_catalogue(self):
        assert catalogue.free_models_for("ollama") == []

    def test_default_role_bindings_resolve_under_the_cli_provider(self):
        """Characters must not silently fall back to Ollama's model."""
        oracle = catalogue.default_for_role("oracle", "opencode-cli")
        assert oracle == catalogue.OPENCODE_ROLE_DEFAULTS["ORACLE"]

    def test_ollama_still_gets_ollama_defaults(self):
        assert (catalogue.default_for_role("oracle", "ollama")
                == catalogue.OLLAMA_ROLE_DEFAULTS["ORACLE"])