from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID, uuid4
from datetime import datetime

from db.postgres import get_db
from db.models import Session, Deliberation, User
from logging_config import get_logger

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
logger = get_logger(__name__)


class SessionCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=512)
    topic: Optional[str] = Field(default=None)

class SessionResponse(BaseModel):
    id: UUID
    title: Optional[str]
    topic: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


async def _get_or_create_user(db: AsyncSession) -> User:
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()
    if not user:
        import os
        user = User(id=uuid4(), name=os.environ.get("USER", "Overseer"))
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(body: SessionCreate, db: AsyncSession = Depends(get_db)):
    user = await _get_or_create_user(db)
    session = Session(
        id=uuid4(),
        user_id=user.id,
        title=body.title or f"Deliberation {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
        topic=body.topic,
        status="active",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    logger.info("session_created", session_id=str(session.id))
    return session


@router.get("", response_model=List[SessionResponse])
async def list_sessions(
    offset: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Session)
        .order_by(Session.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.delete("/{session_id}", status_code=204)
async def archive_session(session_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.status = "archived"
    await db.commit()
    logger.info("session_archived", session_id=str(session_id))


class DeliberateStart(BaseModel):
    query: str = Field(..., min_length=1, max_length=10000)


@router.post("/{session_id}/deliberate", status_code=202)
async def start_session_deliberation(
    session_id: UUID,
    body: DeliberateStart,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    from api.deliberations import _run_deliberation

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
        str(deliberation.id),
        str(session_id),
        body.query,
    )

    session.updated_at = datetime.utcnow()
    await db.commit()

    return {
        "deliberation_id": str(deliberation.id),
        "session_id": str(session_id),
        "status": "accepted",
    }
