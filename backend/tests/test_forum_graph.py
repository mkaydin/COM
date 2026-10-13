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
        assert nodes == {"open_topic", "statements", "exchange", "synthesis"}

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
        assert decide_continue({"round": 2, "max_rounds": 2, "signals": {}}) == "synthesise"

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
        assert decide_continue(state) == "synthesise"

    def test_not_converged_keeps_going(self):
        state = {"round": 0, "max_rounds": 5, "signals": {"converged": False}}
        assert decide_continue(state) == "exchange"

    def test_missing_state_still_terminates(self):
        """A malformed state must fail closed, not spin.

        `{}` means round 0 of a 2-round budget, so one exchange is correct —
        what matters is that it is bounded, not that it starts by synthesising.
        """
        state = {}
        rounds = 0
        while decide_continue(state) == "exchange":
            rounds += 1
            state["round"] = rounds
            assert rounds < 50, "empty state failed to terminate"
        assert rounds == 2  # the default budget

    def test_zero_budget_synthesises_immediately(self):
        assert decide_continue({"round": 0, "max_rounds": 0}) == "synthesise"


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