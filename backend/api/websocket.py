import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from uuid import UUID

from agents.graph import get_graph, CouncilState
from logging_config import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.websocket("/ws/sessions/{session_id}/stream")
async def deliberation_stream(websocket: WebSocket, session_id: str):
    await websocket.accept()
    logger.info("ws_connected", session_id=session_id)

    try:
        while True:
            raw = await websocket.receive_text()
            message = json.loads(raw)

            if message.get("type") == "query":
                query = message.get("content", "")
                if not query:
                    await websocket.send_json({"type": "error", "message": "Empty query"})
                    continue

                logger.info("ws_deliberation_started", session_id=session_id, query=query[:100])

                graph = get_graph()
                state: CouncilState = {
                    "user_query": query,
                    "session_id": session_id,
                    "deliberation_id": session_id,
                    "critique_round": 0,
                    "max_critique_rounds": 2,
                    "current_round": 1,
                    "status": "running",
                    "should_critique": False,
                    "unresolved_conflicts": [],
                    "user_overridden": False,
                }

                try:
                    result = await graph.ainvoke(state)

                    for agent_name, state_key in [
                        ("scout", "scout_response"),
                        ("historian", "historian_response"),
                        ("detective", "detective_response"),
                        ("sage", "sage_response"),
                    ]:
                        data = result.get(state_key)
                        if data:
                            await websocket.send_json({
                                "type": "agent_response",
                                "agent": agent_name,
                                "response": data,
                            })

                    moderator_data = result.get("moderator_synthesis")
                    if moderator_data:
                        await websocket.send_json({
                            "type": "moderator_summary",
                            "content": moderator_data,
                        })

                    companion_data = result.get("companion_notes")
                    if companion_data:
                        await websocket.send_json({
                            "type": "companion_note",
                            "content": companion_data,
                        })

                    await websocket.send_json({
                        "type": "deliberation_complete",
                        "deliberation_id": session_id,
                        "query_type": result.get("query_type", "general"),
                    })

                except Exception as e:
                    logger.error("ws_deliberation_failed", error=str(e))
                    await websocket.send_json({
                        "type": "error",
                        "message": str(e),
                    })

    except WebSocketDisconnect:
        logger.info("ws_disconnected", session_id=session_id)
    except Exception as e:
        logger.error("ws_error", session_id=session_id, error=str(e))
        try:
            await websocket.close()
        except Exception:
            pass
