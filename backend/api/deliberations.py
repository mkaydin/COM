import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from db.postgres import get_db
from db.models import Deliberation, AgentResponse, Critique, Verdict
from logging_config import get_logger

router = APIRouter(prefix="/api", tags=["deliberations"])
logger = get_logger(__name__)


async def _run_deliberation(deliberation_id: str, session_id: str, query: str):
    """Run the LangGraph workflow and persist agent responses.

    Creates its own DB session so it is safe to call from BackgroundTasks
    (the request-scoped session is already closed by the time the task runs).
    """
    import time as time_module
    from agents.graph import get_graph
    from db.postgres import async_session

    logger.info("deliberation_starting", deliberation_id=deliberation_id)
    start_time = time_module.time()

    from agents.session_memory import load_session_memory
    session_memory = await load_session_memory(str(session_id), exclude_deliberation_id=deliberation_id)

    initial_state = {
        "user_query": query,
        "session_id": str(session_id),
        "deliberation_id": deliberation_id,
        "critique_round": 0,
        "max_critique_rounds": 2,
        "current_round": 1,
        "status": "running",
        "session_memory": session_memory,
    }

    try:
        graph = get_graph()
        final_state = await graph.ainvoke(initial_state)
        total_latency = int((time_module.time() - start_time) * 1000)

        # Must match the state keys the graph actually produces — a mismatch
        # here silently drops the Oracle and Scholar responses from the archive.
        agent_map = {
            "oracle": final_state.get("oracle_response"),
            "rag": final_state.get("rag_response"),
            "sage": final_state.get("sage_response"),
            "moderator": final_state.get("moderator_synthesis"),
        }

        async with async_session() as db:
            for agent_name, response_data in agent_map.items():
                if response_data:
                    confidence = response_data.get("confidence") if isinstance(response_data, dict) else None
                    reasoning_type = response_data.get("reasoning_type") if isinstance(response_data, dict) else None

                    agent_resp = AgentResponse(
                        id=uuid.uuid4(),
                        deliberation_id=UUID(deliberation_id),
                        agent_name=agent_name,
                        response_json=response_data,
                        confidence=float(confidence) if confidence else None,
                        reasoning_type=reasoning_type,
                        latency_ms=total_latency,
                    )
                    db.add(agent_resp)

            result = await db.execute(
                select(Deliberation).where(Deliberation.id == UUID(deliberation_id))
            )
            deliberation = result.scalar_one_or_none()
            if deliberation:
                deliberation.status = "completed"
                deliberation.query_type = final_state.get("query_type", "general")

            await db.commit()
            logger.info("deliberation_completed", deliberation_id=deliberation_id)

    except Exception as e:
        logger.error("deliberation_failed", deliberation_id=deliberation_id, error=str(e))
        async with async_session() as db:
            result = await db.execute(
                select(Deliberation).where(Deliberation.id == UUID(deliberation_id))
            )
            deliberation = result.scalar_one_or_none()
            if deliberation:
                deliberation.status = "failed"
            await db.commit()


@router.get("/sessions/{session_id}/deliberations")
async def list_deliberations(session_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Deliberation)
        .where(Deliberation.session_id == session_id)
        .order_by(Deliberation.created_at.desc())
    )
    deliberations = result.scalars().all()
    return [
        {
            "id": str(d.id),
            "round": d.round,
            "query": d.user_query,
            "query_type": d.query_type,
            "status": d.status,
            "created_at": str(d.created_at),
        }
        for d in deliberations
    ]


@router.get("/deliberations/{deliberation_id}")
async def get_deliberation(deliberation_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Deliberation).where(Deliberation.id == deliberation_id)
    )
    deliberation = result.scalar_one_or_none()
    if not deliberation:
        raise HTTPException(status_code=404, detail="Deliberation not found")

    responses_result = await db.execute(
        select(AgentResponse)
        .where(AgentResponse.deliberation_id == str(deliberation_id))
        .order_by(AgentResponse.created_at)
    )
    responses = responses_result.scalars().all()

    return {
        "id": str(deliberation.id),
        "session_id": str(deliberation.session_id),
        "round": deliberation.round,
        "query": deliberation.user_query,
        "query_type": deliberation.query_type,
        "status": deliberation.status,
        "created_at": str(deliberation.created_at),
        "responses": [
            {
                "id": str(r.id),
                "agent": r.agent_name,
                "confidence": r.confidence,
                "reasoning_type": r.reasoning_type,
                "response": r.response_json,
                "created_at": str(r.created_at),
            }
            for r in responses
        ],
    }


@router.get("/deliberations/{deliberation_id}/export/pdf")
async def export_deliberation_pdf(deliberation_id: UUID, db: AsyncSession = Depends(get_db)):
    from fastapi.responses import Response

    result = await db.execute(
        select(Deliberation).where(Deliberation.id == deliberation_id)
    )
    deliberation = result.scalar_one_or_none()
    if not deliberation:
        raise HTTPException(status_code=404, detail="Deliberation not found")

    responses_result = await db.execute(
        select(AgentResponse)
        .where(AgentResponse.deliberation_id == str(deliberation_id))
        .order_by(AgentResponse.created_at)
    )
    responses = responses_result.scalars().all()

    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Council of Minds — Deliberation Record", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Date: {deliberation.created_at.strftime('%Y-%m-%d %H:%M')}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Status: {deliberation.status}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Query type: {deliberation.query_type or 'general'}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    pdf.set_draw_color(200, 170, 110)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 10, "Initial Query", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "I", 11)
    pdf.multi_cell(0, 6, f'"{deliberation.user_query}"')
    pdf.ln(4)

    pdf.set_draw_color(200, 170, 110)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)

    for r in responses:
        pdf.set_font("Helvetica", "B", 12)
        agent_label = r.agent_name.capitalize()
        if r.confidence is not None:
            agent_label += f" (confidence: {round(r.confidence * 100)}%)"
        pdf.cell(0, 8, agent_label, new_x="LMARGIN", new_y="NEXT")

        resp_text = ""
        if isinstance(r.response_json, dict):
            resp_text = r.response_json.get("text") or r.response_json.get("synthesis") or r.response_json.get("analysis") or ""
        elif isinstance(r.response_json, str):
            resp_text = r.response_json

        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, resp_text)
        pdf.ln(3)

    pdf.set_draw_color(200, 170, 110)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)

    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 6, "Generated by Council of Minds", align="C", new_x="LMARGIN", new_y="NEXT")

    return Response(content=pdf.output(), media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="deliberation_{deliberation_id}.pdf"'
    })


@router.get("/deliberations/{deliberation_id}/status")
async def get_deliberation_status(deliberation_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Deliberation).where(Deliberation.id == deliberation_id)
    )
    deliberation = result.scalar_one_or_none()
    if not deliberation:
        raise HTTPException(status_code=404, detail="Deliberation not found")

    responses_result = await db.execute(
        select(AgentResponse)
        .where(AgentResponse.deliberation_id == str(deliberation_id))
    )
    count = len(responses_result.scalars().all())

    return {
        "id": str(deliberation.id),
        "status": deliberation.status,
        "round": deliberation.round,
        "query": deliberation.user_query,
        "agent_count": count,
    }


@router.delete("/deliberations/{deliberation_id}", status_code=204)
async def delete_deliberation(deliberation_id: UUID, db: AsyncSession = Depends(get_db)):
    """Delete a deliberation and all associated rows (verdict, critiques, responses).

    Order matters: verdicts reference both deliberations AND agent_responses
    (NO ACTION FKs), so verdicts must be removed before agent_responses, and
    everything must be removed before the deliberation itself.
    """
    result = await db.execute(
        select(Deliberation).where(Deliberation.id == deliberation_id)
    )
    deliberation = result.scalar_one_or_none()
    if not deliberation:
        raise HTTPException(status_code=404, detail="Deliberation not found")

    verdicts = await db.execute(
        select(Verdict).where(Verdict.deliberation_id == str(deliberation_id))
    )
    for v in verdicts.scalars().all():
        await db.delete(v)

    critiques = await db.execute(
        select(Critique).where(Critique.deliberation_id == str(deliberation_id))
    )
    for c in critiques.scalars().all():
        await db.delete(c)

    responses = await db.execute(
        select(AgentResponse).where(AgentResponse.deliberation_id == str(deliberation_id))
    )
    for resp in responses.scalars().all():
        await db.delete(resp)

    await db.delete(deliberation)
    await db.commit()
    logger.info("deliberation_deleted", deliberation_id=str(deliberation_id))
