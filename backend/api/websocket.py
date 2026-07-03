"""
WebSocket endpoint for real-time deliberation streaming.

Flow:
  Client connects → sends query → server runs LangGraph with astream()
  → yields per-node updates as they complete → client renders cards live
"""
import json
import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from logging_config import get_logger

router = APIRouter(tags=["websocket"])
logger = get_logger(__name__)

AGENT_NODES = {"oracle", "rag", "sage"}


@router.websocket("/ws/sessions/{session_id}/stream")
async def deliberation_stream(websocket: WebSocket, session_id: str):
    await websocket.accept()
    logger.info("ws_connected", session_id=session_id)

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "query":
                query = msg.get("content", "")
                target_agent = msg.get("target_agent", None)
                provider = msg.get("provider", "glm")
                await _run_streaming_deliberation(websocket, session_id, query, target_agent, provider)

            elif msg.get("type") == "verdict":
                await _handle_verdict(websocket, session_id, msg)

    except WebSocketDisconnect:
        logger.info("ws_disconnected", session_id=session_id)


async def _run_streaming_deliberation(websocket: WebSocket, session_id: str, query: str, target_agent: str = None, provider: str = "glm"):
    """Run the LangGraph workflow with streaming, sending updates via WebSocket."""
    import time as time_module
    from agents.graph import get_graph
    from db.postgres import async_session
    from db.models import Deliberation, AgentResponse
    from uuid import UUID

    deliberation_id = str(uuid.uuid4())
    node_times = {}
    collected_outputs = {}

    # Create the Deliberation record immediately so it exists for verdicts
    try:
        async with async_session() as db:
            db.add(Deliberation(
                id=UUID(deliberation_id),
                session_id=session_id,
                round=1,
                user_query=query,
                status="running",
            ))
            await db.commit()
    except Exception as e:
        logger.error("deliberation_create_failed", error=str(e))

    await websocket.send_text(json.dumps({
        "type": "deliberation_started",
        "deliberation_id": deliberation_id,
        "query": query,
    }))

    for agent in AGENT_NODES:
        node_times[agent] = time_module.time()
        await websocket.send_text(json.dumps({
            "type": "agent_update",
            "agent": agent,
            "status": "thinking",
        }))

    # Load a compact summary of earlier deliberations in this session so the
    # moderator can stay coherent across follow-up questions.
    from agents.session_memory import load_session_memory
    session_memory = await load_session_memory(session_id, exclude_deliberation_id=deliberation_id)

    initial_state = {
        "user_query": query,
        "session_id": session_id,
        "deliberation_id": deliberation_id,
        "critique_round": 0,
        "max_critique_rounds": 2,
        "current_round": 1,
        "status": "running",
        "target_agent": target_agent,
        "provider": provider,
        "session_memory": session_memory,
    }

    try:
        graph = get_graph()

        async for update in graph.astream(initial_state, stream_mode="updates"):
            for node_name, node_output in update.items():
                if node_output is None:
                    continue
                if node_name in AGENT_NODES and isinstance(node_output, dict):
                    node_output["_latency_ms"] = int((time_module.time() - node_times.get(node_name, time_module.time())) * 1000)
                collected_outputs[node_name] = node_output
                await _send_node_update(websocket, node_name, node_output, deliberation_id)

        await _persist_responses(deliberation_id, session_id, query, collected_outputs, node_times)

        await websocket.send_text(json.dumps({
            "type": "deliberation_complete",
            "deliberation_id": deliberation_id,
        }))

    except Exception as e:
        logger.error("ws_deliberation_failed", error=str(e))
        # Mark deliberation as failed
        try:
            async with async_session() as db:
                from sqlalchemy import select
                result = await db.execute(select(Deliberation).where(Deliberation.id == UUID(deliberation_id)))
                d = result.scalar_one_or_none()
                if d:
                    d.status = "failed"
                    await db.commit()
        except:
            pass
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"Deliberation failed: {str(e)}",
        }))


async def _send_node_update(websocket: WebSocket, node_name: str, node_output: dict, deliberation_id: str):
    """Send the appropriate WebSocket message for a completed node."""
    if not isinstance(node_output, dict):
        return

    if node_name == "router":
        await websocket.send_text(json.dumps({
            "type": "router_update",
            "query_type": node_output.get("query_type", "general"),
            "agent_priority": node_output.get("agent_priority", []),
        }))

    elif node_name in AGENT_NODES:
        response_key = f"{node_name}_response"
        response_data = node_output.get(response_key, node_output)

        await websocket.send_text(json.dumps({
            "type": "agent_update",
            "agent": node_name,
            "status": "done",
            "response": response_data,
            "confidence": response_data.get("confidence", 0) if isinstance(response_data, dict) else 0,
        }))

    elif node_name == "moderator":
        synth = node_output.get("moderator_synthesis", {})
        await websocket.send_text(json.dumps({
            "type": "moderator_summary",
            "content": synth,
            "should_critique": node_output.get("should_critique", False),
            "unresolved_conflicts": node_output.get("unresolved_conflicts", []),
            "post_critique": node_output.get("post_critique", False),
        }))

    elif node_name == "companion":
        notes = node_output.get("companion_notes", {})
        await websocket.send_text(json.dumps({
            "type": "companion_note",
            "content": notes,
        }))

    elif node_name == "critique":
        await websocket.send_text(json.dumps({
            "type": "critique_round",
            "round": node_output.get("critique_round", 0),
            "critiques": node_output.get("critiques", []),
        }))


async def _persist_responses(deliberation_id: str, session_id: str, query: str, collected_outputs: dict, node_times: dict = None):
    """Save agent responses, critiques, and update deliberation status."""
    from db.postgres import async_session
    from db.models import Deliberation, AgentResponse, Critique
    from uuid import UUID

    try:
        agent_map = {
            "oracle": collected_outputs.get("oracle", {}).get("oracle_response"),
            "rag": collected_outputs.get("rag", {}).get("rag_response"),
            "sage": collected_outputs.get("sage", {}).get("sage_response"),
            "moderator": collected_outputs.get("moderator", {}).get("moderator_synthesis"),
        }

        query_type = collected_outputs.get("router", {}).get("query_type", "general")

        async with async_session() as db:
            from sqlalchemy import select
            result = await db.execute(select(Deliberation).where(Deliberation.id == UUID(deliberation_id)))
            deliberation = result.scalar_one_or_none()
            if deliberation:
                deliberation.status = "completed"
                deliberation.query_type = query_type

            for agent_name, response_data in agent_map.items():
                if response_data:
                    confidence = response_data.get("confidence") if isinstance(response_data, dict) else None
                    reasoning_type = response_data.get("reasoning_type") if isinstance(response_data, dict) else None
                    latency_ms = response_data.get("_latency_ms") if isinstance(response_data, dict) else None
                    db.add(AgentResponse(
                        id=uuid.uuid4(),
                        deliberation_id=UUID(deliberation_id),
                        agent_name=agent_name,
                        response_json=response_data,
                        confidence=float(confidence) if confidence else None,
                        reasoning_type=reasoning_type,
                        latency_ms=latency_ms,
                    ))

            # Persist the critique round(s) so they show up in the Archive / deliberation view
            critique_out = collected_outputs.get("critique")
            if isinstance(critique_out, dict):
                base_round = critique_out.get("critique_round", 1)
                for c in critique_out.get("critiques", []) or []:
                    round_n = c.get("round", base_round)
                    resolved = bool(c.get("resolved"))
                    topic = c.get("topic", "")
                    members = c.get("critiques", []) or []
                    if not members:
                        db.add(Critique(
                            id=uuid.uuid4(), deliberation_id=UUID(deliberation_id),
                            critique_round=round_n, source_agent="moderator", target_agent="council",
                            critique_text=f"[resolved] {topic}", resolved=resolved,
                        ))
                    for m in members:
                        src = m.get("agent") or "council"
                        stance = m.get("stance", "hold")
                        text = m.get("text", "")
                        db.add(Critique(
                            id=uuid.uuid4(), deliberation_id=UUID(deliberation_id),
                            critique_round=round_n, source_agent=src, target_agent="council",
                            critique_text=f"[{stance}] {text}".strip(), resolved=resolved,
                        ))

            await db.commit()
            logger.info("deliberation_persisted", deliberation_id=deliberation_id)

    except Exception as e:
        logger.error("persist_failed", error=str(e))


async def _handle_verdict(websocket: WebSocket, session_id: str, msg: dict):
    """Handle user verdict submitted via WebSocket."""
    from db.postgres import async_session
    from db.models import Verdict, Deliberation
    from uuid import UUID

    deliberation_id = msg.get("deliberation_id", "")
    chosen_agent = msg.get("chosen_agent")
    user_override = msg.get("user_override")
    verdict_type = msg.get("verdict_type", "accepted")

    try:
        async with async_session() as db:
            # Verify deliberation exists first
            result = await db.execute(
                select(Deliberation).where(Deliberation.id == UUID(deliberation_id))
            )
            deliberation = result.scalar_one_or_none()
            if not deliberation:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Deliberation not found — cannot save verdict.",
                }))
                return

            verdict = Verdict(
                id=uuid.uuid4(),
                deliberation_id=UUID(deliberation_id),
                user_override=user_override,
                verdict_type=verdict_type,
            )
            db.add(verdict)

            deliberation.status = "completed"
            if chosen_agent:
                deliberation.query_type = chosen_agent

            await db.commit()

        await websocket.send_text(json.dumps({
            "type": "verdict_saved",
            "deliberation_id": deliberation_id,
            "verdict_type": verdict_type,
        }))
    except Exception as e:
        logger.error("ws_verdict_failed", error=str(e))
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"Verdict save failed: {str(e)}",
        }))
