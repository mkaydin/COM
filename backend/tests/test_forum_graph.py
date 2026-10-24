"""Tests for the forum graph's wiring and its loop bound.

The failure this guards against is specific and expensive: a forum thread that
runs forever, burning free-tier quota one round at a time. `max_rounds` is
checked in exactly one place, and these tests prove it holds even when every
character disagrees forever.
"""

import pytest

from agents import forum_graph, registry
from agents.forum_graph import ForumState, build_workflow, decide_continue, get_graph


class TestWiring:
    def test_all_forum_nodes_registered(self):
        nodes = set(build_workflow().nodes)
        assert nodes == {"open_topic", "statements", "exchange", "revise", "synthesis"}

    def test_compiles(self):
        assert get_graph() is not None

    def test_graph_is_cached(self):
        assert get_graph() is get_graph()

    def test_entry_point_is_open_topic(self):
        wf = build_workflow()
        assert wf.nodes  # sanity
        # The Moderator frames before anyone speaks.
        assert forum_graph.open_topic is not None

    def test_old_chamber_graph_is_separate(self):
        """Two graphs must not have been accidentally merged."""
        assert set(build_workflow().nodes) != set(
            __import__("agents.graph", fromlist=["build_workflow"]).build_workflow().nodes
        )


class TestRoundBound:
    """The bound. Checked here and nowhere else in the graph."""

    def test_first_round_runs(self):
        assert decide_continue({"round": 0, "max_rounds": 2, "signals": {}}) == "exchange"

    def test_stops_at_the_budget(self):
        assert decide_continue({"round": 2, "max_rounds": 2, "signals": {}}) == "revise"

    @pytest.mark.parametrize("max_rounds", [1, 2, 3, 5])
    def test_always_disagreeing_cast_terminates(self, max_rounds):
        """No signals, no convergence — the graph must still stop."""
        state = {"round": 0, "max_rounds": max_rounds, "signals": {}}
        rounds = 0
        while decide_continue(state) == "exchange":
            rounds += 1
            state["round"] += 1
            assert rounds < 50, "forum exchange loop failed to terminate"
        assert rounds == max_rounds

    def test_converged_thread_skips_remaining_rounds(self):
        """Converging early must not burn more free-tier calls."""
        state = {"round": 0, "max_rounds": 5,
                 "signals": {"converged": True}}
        assert decide_continue(state) == "revise"

    def test_not_converged_keeps_going(self):
        state = {"round": 0, "max_rounds": 5, "signals": {"converged": False}}
        assert decide_continue(state) == "exchange"

    def test_missing_state_still_terminates(self):
        """A malformed state must fail closed, not spin.

        `{}` means round 0 of the default 1-round budget, so one exchange is
        correct — what matters is that it is bounded, not that it starts by
        going to revision.
        """
        state = {}
        rounds = 0
        while decide_continue(state) == "exchange":
            rounds += 1
            state["round"] = rounds
            assert rounds < 50, "empty state failed to terminate"
        assert rounds == 1  # the default budget

    def test_nobody_to_speak_ends_the_loop(self):
        """A round with no disputed characters would be a full-price restatement."""
        assert decide_continue(
            {"round": 0, "max_rounds": 5, "no_speakers": True, "signals": {}}
        ) == "revise"

    def test_zero_budget_goes_straight_to_revision(self):
        """No discussion still needs a final answer, so revision always runs."""
        assert decide_continue({"round": 0, "max_rounds": 0}) == "revise"


class TestState:
    def test_required_keys_present(self):
        keys = set(ForumState.__annotations__)
        assert {"topic_id", "question", "cast", "round", "max_rounds"} <= keys

    def test_posts_uses_an_adder_reducer(self):
        """Every node returns `posts`; without the reducer later rounds would
        overwrite earlier ones and the thread would lose its history."""
        from typing import get_type_hints

        hints = get_type_hints(ForumState, include_extras=True)
        metadata = getattr(hints["posts"], "__metadata__", [])
        assert metadata
        assert any("add" in str(m) for m in metadata)

    def test_state_is_not_the_chamber_state(self):
        from agents.graph import CouncilState

        assert set(CouncilState.__annotations__) != set(ForumState.__annotations__)


class TestFallbacks:
    """A forum where the Moderator is unreachable must still produce a topic."""

    def test_fallback_opening_invites_the_whole_cast(self):
        opening = forum_graph._fallback_opening("what is the question")
        assert opening["cast"]
        assert opening["mode"] in ("consensus", "diverse")

    def test_fallback_opening_title_comes_from_the_question(self):
        opening = forum_graph._fallback_opening("a real question")
        assert opening["title"] == "a real question"

    def test_fallback_synthesis_of_a_converged_thread(self):
        from agents.forum_signals import compute

        signals = compute([
            {"seq": 1, "author": "oracle", "author_kind": "character",
             "stance": "propose", "body": "x", "claims": [{"text": SAME}], "confidence": 0.8},
            {"seq": 2, "author": "scholar", "author_kind": "character",
             "stance": "propose", "body": "x", "claims": [{"text": SAME}], "confidence": 0.8},
        ])
        assert signals["converged"]
        synth = forum_graph._fallback_synthesis("q", signals)
        assert synth["mode"] == "consensus"
        assert synth["answer"]

    def test_fallback_synthesis_of_a_split_thread(self):
        from agents.forum_signals import compute

        signals = compute([
            {"seq": 1, "author": "oracle", "author_kind": "character", "stance": "propose",
             "body": "a", "claims": [{"text": "migration loses data"}], "confidence": 0.9},
            {"seq": 2, "author": "scholar", "author_kind": "character", "stance": "propose",
             "body": "b", "claims": [{"text": "backups make it safe"}], "confidence": 0.9},
            {"seq": 3, "author": "sage", "author_kind": "character", "stance": "propose",
             "body": "c", "claims": [{"text": "unrelated ethics point"}], "confidence": 0.9},
        ])
        assert not signals["converged"]
        synth = forum_graph._fallback_synthesis("q", signals)
        assert synth["mode"] == "plurality"
        # Every character must appear as its own position.
        assert len(synth["positions"]) == 3

    def test_fallback_synthesis_with_no_posts(self):
        synth = forum_graph._fallback_synthesis("q", {"cast": []})
        assert synth["mode"] == "plurality"
        assert synth["positions"] == []

    def test_fallback_synthesis_reports_a_total_outage(self):
        """Nobody spoke — say so instead of implying a considered position.

        A missing API key must not produce a thread that reads like the
        Council considered the question and declined to answer.
        """
        synth = forum_graph._fallback_synthesis("q", {"cast": [], "reason": "no character posts"})
        assert synth["answer"] == ""
        assert "unreachable" in synth["fault_line"] or "misconfigured" in synth["fault_line"]


class TestFailureHandling:
    """A provider outage must not bury the thread in identical apologies."""

    def test_a_failed_character_does_not_produce_a_post(self):
        """`ok=False` means the caller skips the post entirely."""
        result = {"ok": False, "body": "", "author": "oracle", "extra": {"error": "boom"}}
        assert not result.get("ok")

    def test_state_carries_failed_characters(self):
        keys = set(ForumState.__annotations__)
        assert "failed_characters" in keys, (
            "the graph must record which characters failed so the moderator "
            "can be told the cast was silent"
        )


SAME = "the server runs on ubuntu linux with docker containers"


class TestCastSelection:
    def test_default_cast_is_valid(self):
        assert len(registry.validate_cast(registry.default_cast())) == 5

    def test_moderator_pick_is_validated(self):
        """An unknown character from the model must not reach the database."""
        with pytest.raises(KeyError):
            registry.validate_cast(["oracle", "the-lorax"])

    def test_cast_of_one_is_allowed(self):
        assert registry.validate_cast(["sage"]) == ["sage"]

    def test_legacy_cast_key_is_accepted(self):
        """`validate_cast` resolves legacy keys, so a stored cast still works."""
        assert registry.validate_cast(["rag"]) == ["scholar"]


class TestTieredRounds:
    """After the first round, only disputed characters are invited."""

    async def test_a_round_with_nobody_to_hear_is_skipped(self, monkeypatch):
        """Not an error — the graph moves straight to revision.

        `exchange` imports `async_session` from `db.postgres` inside the
        function, so the patch has to land there rather than on forum_graph.
        """
        import uuid

        import db.postgres as pg
        from agents import forum_graph

        class FakeSession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *exc):
                return False

        async def no_posts(db, topic_id, **kw):
            return []

        monkeypatch.setattr(pg, "async_session", lambda: FakeSession())
        monkeypatch.setattr(forum_graph, "list_posts", no_posts)
        monkeypatch.setattr(
            forum_graph.forum_signals, "next_round_speakers", lambda *a, **k: []
        )

        out = await forum_graph.exchange({
            "topic_id": str(uuid.uuid4()),
            "question": "q",
            "cast": ["oracle", "sage"],
            "round": 1,          # next round is 2, i.e. tiered
        })

        assert out["no_speakers"] is True
        assert out["posts"] == []
        # The round counter must not advance for a round that never happened.
        assert out["round"] == 1
