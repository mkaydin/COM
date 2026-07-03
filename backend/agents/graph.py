from typing import Optional, List, Annotated
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, END
import operator

from agents.router import router_node
from agents.oracle import oracle_node
from agents.sage import sage_node
from agents.rag_agent import rag_node
from agents.moderator import moderator_node
from agents.critique import critique_round_node
from logging_config import get_logger

logger = get_logger(__name__)


class CouncilState(TypedDict, total=False):
    user_query: str
    session_id: str
    deliberation_id: str

    oracle_response: Optional[dict]
    rag_response: Optional[dict]
    historian_response: Optional[dict]
    detective_response: Optional[dict]
    sage_response: Optional[dict]
    moderator_synthesis: Optional[dict]
    companion_notes: Optional[dict]

    critique_round: int
    max_critique_rounds: int
    unresolved_conflicts: List[dict]
    critiques: Annotated[List[dict], operator.add]

    current_round: int
    status: str
    should_critique: bool

    query_type: str
    agent_priority: List[str]

    session_memory: str

    user_verdict: Optional[dict]
    user_overridden: bool


def decide_critique(state: CouncilState) -> str:
    should = state.get("should_critique", False)
    round_num = state.get("critique_round", 0)
    max_rounds = state.get("max_critique_rounds", 2)
    if should and round_num < max_rounds:
        return "critique"
    return "proceed"


def build_workflow() -> StateGraph:
    workflow = StateGraph(CouncilState)

    workflow.add_node("router", router_node)
    workflow.add_node("oracle", oracle_node)
    workflow.add_node("rag", rag_node)
    workflow.add_node("sage", sage_node)
    workflow.add_node("moderator", moderator_node)
    workflow.add_node("critique", critique_round_node)

    workflow.set_entry_point("router")

    workflow.add_edge("router", "oracle")
    workflow.add_edge("router", "rag")
    workflow.add_edge("router", "sage")

    workflow.add_edge("oracle", "moderator")
    workflow.add_edge("rag", "moderator")
    workflow.add_edge("sage", "moderator")

    workflow.add_conditional_edges("moderator", decide_critique, {
        "critique": "critique",
        "proceed": END
    })

    # After a critique round, re-run the moderator so it can fold the debate
    # outcomes (concessions / revisions / resolutions) into an updated synthesis.
    # The loop is bounded by decide_critique (should_critique + critique_round < max).
    workflow.add_edge("critique", "moderator")

    return workflow


_graph = None


def get_graph() -> StateGraph:
    global _graph
    if _graph is None:
        _graph = build_workflow().compile()
        logger.info("langgraph_workflow_compiled")
    return _graph
