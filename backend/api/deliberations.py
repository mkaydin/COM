from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID, uuid4
from datetime import datetime

from db.postgres import get_db
from db.models import Session, Deliberation, AgentResponse
from agents.graph import get_graph, CouncilState
from logging_config import get_logger

router = APIRouter(prefix="/api/deliberations", tags=["deliberations"])
logger = get_logger(__name__)


class DeliberationStart(BaseModel):
    query: str = Field(..., min_length=1, max_length=10000)


class DeliberationListResponse(BaseModel):
    id: UUID
    session_id: UUID
    round: int
    user_query: str
    query_type: Optional[str]
    status: str
    created_at: datetime
    model_config = {"from_attributes": True}


class AgentResponseOut(BaseModel):
    agent_name: str
    confidence: Optional[float]
    reasoning_type: Optional[str]
    response_json: dict
    model_config = {"from_attributes": True}


class DeliberationDetailResponse(BaseModel):
    id: UUID
    session_id: UUID
    round: int
    user_query: str
    query_type: Optional[str]
    status: str
    created_at: datetime
    agent_responses: List[AgentResponseOut] = Field(default_factory=list)


class StatusResponse(BaseModel):
    deliberation_id: UUID
    status: str
    scout_confidence: Optional[float] = None
    historian_confidence: Optional[float] = None
    detective_confidence: Optional[float] = None
    sage_confidence: Optional[float] = None


async def _run_deliberation(deliberation_id: UUID, query: str, session_id: str):
    logger.info("deliberation_started", deliberation_id=str(deliberation_id), query=query[:100])

    graph = get_graph()
    initial_state: CouncilState = {
        "user_query": query,
        "session_id": session_id,
        "deliberation_id": str(deliberation_id),
        "critique_round": 0,
        "max_critique_rounds": 2,
        "current_round": 1,
        "status": "running",
        "should_critique": False,
        "unresolved_conflicts": [],
        "user_overridden": False,
    }

    try:
        result = await graph.ainvoke(initial_state)
        logger.info("deliberation_completed", deliberation_id=str(deliberation_id))

        from db.postgres import async_session
        async with async_session() as db:
            deliberation = await db.get(Deliberation, deliberation_id)
            if deliberation:
                deliberation.status = "completed"
                deliberation.query_type = result.get("query_type")

                for agent_name, state_key in [
                    ("scout", "scout_response"),
                    ("historian", "historian_response"),
                    ("detective", "detective_response"),
                    ("sage", "sage_response"),
                    ("moderator", "moderator_synthesis"),
                    ("companion", "companion_notes"),
                ]:
                    response_data = result.get(state_key)
                    if response_data:
                        ar = AgentResponse(
                            id=uuid4(),
                            deliberation_id=deliberation_id,
                            agent_name=agent_name,
                            response_json=response_data,
                            confidence=response_data.get("confidence"),
                            reasoning_type=response_data.get("reasoning_type"),
                        )
                        db.add(ar)

                await db.commit()

    except Exception as e:
        logger.error("deliberation_failed", deliberation_id=str(deliberation_id), error=str(e))
        from db.postgres import async_session
        async with async_session() as db:
            deliberation = await db.get(Deliberation, deliberation_id)
            if deliberation:
                deliberation.status = "failed"
                await db.commit()


@router.get("", response_model=List[DeliberationListResponse])
async def list_deliberations(
    session_id: Optional[UUID] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    query = select(Deliberation).order_by(Deliberation.created_at.desc()).limit(limit)
    if session_id:
        query = query.where(Deliberation.session_id == session_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{deliberation_id}", response_model=DeliberationDetailResponse)
async def get_deliberation(deliberation_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Deliberation).where(Deliberation.id == deliberation_id))
    deliberation = result.scalar_one_or_none()
    if not deliberation:
        raise HTTPException(status_code=404, detail="Deliberation not found")

    responses_result = await db.execute(
        select(AgentResponse)
        .where(AgentResponse.deliberation_id == deliberation_id)
        .order_by(AgentResponse.created_at)
    )
    agent_responses = responses_result.scalars().all()

    return DeliberationDetailResponse(
        id=deliberation.id,
        session_id=deliberation.session_id,
        round=deliberation.round,
        user_query=deliberation.user_query,
        query_type=deliberation.query_type,
        status=deliberation.status,
        created_at=deliberation.created_at,
        agent_responses=agent_responses,
    )


@router.get("/{deliberation_id}/status", response_model=StatusResponse)
async def get_deliberation_status(deliberation_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Deliberation).where(Deliberation.id == deliberation_id))
    deliberation = result.scalar_one_or_none()
    if not deliberation:
        raise HTTPException(status_code=404, detail="Deliberation not found")

    status_data = {
        "deliberation_id": deliberation_id,
        "status": deliberation.status,
    }

    responses_result = await db.execute(
        select(AgentResponse).where(AgentResponse.deliberation_id == deliberation_id)
    )
    for ar in responses_result.scalars().all():
        key = f"{ar.agent_name}_confidence"
        if key in StatusResponse.model_fields:
            status_data[key] = ar.confidence

    return status_data


@router.post("/start/{session_id}", response_model=dict, status_code=202)
async def start_deliberation(
    session_id: UUID,
    body: DeliberationStart,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    session_result = await db.execute(select(Session).where(Session.id == session_id))
    session = session_result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    deliberation = Deliberation(
        id=uuid4(),
        session_id=session_id,
        round=1,
        user_query=body.query,
        status="running",
    )
    db.add(deliberation)
    await db.commit()
    await db.refresh(deliberation)

    background_tasks.add_task(
        _run_deliberation,
        deliberation.id,
        body.query,
        str(session_id),
    )

    return {
        "deliberation_id": str(deliberation.id),
        "session_id": str(session_id),
        "status": "accepted",
    }
