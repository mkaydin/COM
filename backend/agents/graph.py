from typing import Optional, List, Annotated
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

from agents.router import router_node
from agents.scout import scout_node
from agents.historian import historian_node
from agents.detective import detective_node
from agents.sage import sage_node
from agents.moderator import moderator_node
from agents.companion import companion_node
from logging_config import get_logger

logger = get_logger(__name__)


class CouncilState(TypedDict, total=False):
    user_query: str
    session_id: str
    deliberation_id: str

    scout_response: Optional[dict]
    historian_response: Optional[dict]
    detective_response: Optional[dict]
    sage_response: Optional[dict]
    moderator_synthesis: Optional[dict]
    companion_notes: Optional[dict]

    critique_round: int
    max_critique_rounds: int
    unresolved_conflicts: List[dict]
    critiques: Annotated[List[dict], add_messages]

    current_round: int
    status: str
    should_critique: bool

    query_type: str
    agent_priority: List[str]

    user_verdict: Optional[dict]
    user_overridden: bool


def decide_critique(state: CouncilState) -> str:
    should = state.get("should_critique", False)
    round_num = state.get("critique_round", 0)
    max_rounds = state.get("max_critique_rounds", 2)
    if should and round_num < max_rounds:
        return "critique"
    return "proceed"


def decide_continue_critique(state: CouncilState) -> str:
    unresolved = state.get("unresolved_conflicts", [])
    round_num = state.get("critique_round", 0)
    max_rounds = state.get("max_critique_rounds", 2)
    if unresolved and round_num < max_rounds:
        return "continue"
    return "done"


async def critique_round_node(state: CouncilState) -> dict:
    round_num = state.get("critique_round", 0) + 1
    logger.info("critique_round", round=round_num)

    unresolved = state.get("unresolved_conflicts", [])

    critiques = []
    for conflict in unresolved:
        topic = conflict.get("topic", "unknown")
        critiques.append({
            "round": round_num,
            "topic": topic,
            "critiques": [
                {"from": "scout", "text": f"Re-evaluating sources for: {topic}"},
                {"from": "historian", "text": f"Checking additional evidence for: {topic}"},
                {"from": "detective", "text": f"Re-examining connections for: {topic}"},
                {"from": "sage", "text": f"Synthesizing broader context for: {topic}"},
            ]
        })

    return {
        "critiques": critiques,
        "critique_round": round_num,
        "should_critique": False,
        "unresolved_conflicts": [],
    }


def build_workflow() -> StateGraph:
    workflow = StateGraph(CouncilState)

    workflow.add_node("router", router_node)
    workflow.add_node("scout", scout_node)
    workflow.add_node("historian", historian_node)
    workflow.add_node("detective", detective_node)
    workflow.add_node("sage", sage_node)
    workflow.add_node("moderator", moderator_node)
    workflow.add_node("companion", companion_node)
    workflow.add_node("critique", critique_round_node)

    workflow.set_entry_point("router")

    workflow.add_edge("router", "scout")
    workflow.add_edge("router", "historian")
    workflow.add_edge("router", "detective")
    workflow.add_edge("router", "sage")

    workflow.add_edge("scout", "moderator")
    workflow.add_edge("historian", "moderator")
    workflow.add_edge("detective", "moderator")
    workflow.add_edge("sage", "moderator")

    workflow.add_conditional_edges("moderator", decide_critique, {
        "critique": "critique",
        "proceed": "companion"
    })

    workflow.add_conditional_edges("critique", decide_continue_critique, {
        "continue": "critique",
        "done": "companion"
    })

    workflow.add_edge("companion", END)

    return workflow


_graph = None


def get_graph() -> StateGraph:
    global _graph
    if _graph is None:
        _graph = build_workflow().compile()
        logger.info("langgraph_workflow_compiled")
    return _graph
