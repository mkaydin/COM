"""Tests for the LangGraph wiring.

The graph decides which agents run, when the Moderator is re-entered, and when
the loop stops. An unbounded critique loop would burn LLM budget forever, and
a mis-registered node silently drops an agent from the Council — both were
completely untested.
"""
import pytest

from agents.graph import CouncilState, build_workflow, decide_critique, get_graph


class TestDecideCritique:
    """The loop bound. `max_critique_rounds` must always be respected."""

    def test_no_critique_requested(self):
        assert decide_critique({"should_critique": False, "critique_round": 0}) == "proceed"

    def test_critique_requested_within_budget(self):
        assert decide_critique({"should_critique": True, "critique_round": 0}) == "critique"
        assert decide_critique({"should_critique": True, "critique_round": 1}) == "critique"

    def test_critique_blocked_at_max_rounds(self):
        state = {"should_critique": True, "critique_round": 2, "max_critique_rounds": 2}
        assert decide_critique(state) == "proceed"

    def test_respects_a_custom_budget(self):
        assert decide_critique(
            {"should_critique": True, "critique_round": 1, "max_critique_rounds": 1}
        ) == "proceed"

    def test_never_exceeds_the_budget(self):
        """Even a moderator that always asks for critique must terminate."""
        rounds = 0
        state = {"should_critique": True, "critique_round": 0, "max_critique_rounds": 3}
        while decide_critique(state) == "critique":
            rounds += 1
            state["critique_round"] += 1
            assert rounds <= 10, "critique loop failed to terminate"
        assert rounds == 3

    def test_defaults_when_state_is_empty(self):
        assert decide_critique({}) == "proceed"


class TestWorkflowWiring:
    def test_all_council_nodes_registered(self):
        workflow = build_workflow()
        nodes = set(workflow.nodes)
        assert {"router", "oracle", "rag", "sage", "moderator", "critique"} <= nodes

    def test_dead_agents_are_not_registered(self):
        """Scout/Historian/Detective/Companion were removed; a regression here
        would resurrect agents that no longer have schemas."""
        nodes = set(build_workflow().nodes)
        assert not (nodes & {"scout", "historian", "detective", "companion"})

    def test_compiles(self):
        assert get_graph() is not None


class TestStateSchema:
    def test_council_response_keys_present(self):
        keys = set(CouncilState.__annotations__)
        assert {"oracle_response", "rag_response", "sage_response",
                "moderator_synthesis"} <= keys

    def test_dead_agent_keys_removed(self):
        keys = set(CouncilState.__annotations__)
        assert not (keys & {"historian_response", "detective_response", "companion_notes"})

    def test_provider_is_not_part_of_state(self):
        """The Council is Ollama-only; provider must not be threaded through."""
        assert "provider" not in CouncilState.__annotations__

    def test_agent_priority_removed(self):
        assert "agent_priority" not in CouncilState.__annotations__

    def test_critiques_uses_an_adder_reducer(self):
        """Critique rounds loop back into the moderator, so every round's
        entries must accumulate rather than overwrite."""
        from typing import get_type_hints
        hints = get_type_hints(CouncilState, include_extras=True)
        metadata = getattr(hints["critiques"], "__metadata__", [])
        assert metadata, "critiques must carry an Annotated reducer"
        assert any("operator.add" in str(getattr(m, "__origin__", "") or "") or
                   "add" in str(m) for m in metadata)


class TestRouterOutput:
    def test_router_only_reports_query_type(self):
        from agents.router import router_node
        import inspect
        src = inspect.getsource(router_node)
        # agent_priority was consumed only by the removed Companion agent.
        assert "agent_priority" not in src
