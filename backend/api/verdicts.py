from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID, uuid4

from db.postgres import get_db
from db.models import Deliberation, Verdict
from logging_config import get_logger

router = APIRouter(prefix="/api/verdicts", tags=["verdicts"])
logger = get_logger(__name__)


class VerdictSubmit(BaseModel):
    deliberation_id: UUID
    chosen_agent: Optional[str] = Field(default=None)
    chosen_response_id: Optional[UUID] = Field(default=None)
    user_override: Optional[str] = Field(default=None, max_length=10000)


class VerdictResponse(BaseModel):
    id: UUID
    deliberation_id: UUID
    chosen_response_id: Optional[UUID] = None
    user_override: Optional[str] = None
    verdict_type: str
    created_at: str

    model_config = {"from_attributes": True}


@router.post("", response_model=VerdictResponse, status_code=201)
async def submit_verdict(body: VerdictSubmit, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Deliberation).where(Deliberation.id == body.deliberation_id))
    deliberation = result.scalar_one_or_none()
    if not deliberation:
        raise HTTPException(status_code=404, detail="Deliberation not found")

    verdict_type = "overridden" if body.user_override else "accepted"

    verdict = Verdict(
        id=uuid4(),
        deliberation_id=body.deliberation_id,
        chosen_response_id=body.chosen_response_id,
        user_override=body.user_override,
        verdict_type=verdict_type,
    )

    existing = await db.execute(
        select(Verdict).where(Verdict.deliberation_id == body.deliberation_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Verdict already exists for this deliberation")

    db.add(verdict)
    await db.commit()
    await db.refresh(verdict)
    logger.info("verdict_submitted", deliberation_id=str(body.deliberation_id), type=verdict_type)
    return verdict


@router.get("/{deliberation_id}", response_model=VerdictResponse)
async def get_verdict(deliberation_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Verdict).where(Verdict.deliberation_id == deliberation_id)
    )
    verdict = result.scalar_one_or_none()
    if not verdict:
        raise HTTPException(status_code=404, detail="No verdict yet for this deliberation")
    return verdict
