from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from uuid import UUID, uuid4

from db.postgres import get_db
from db.models import Verdict, Deliberation, AgentResponse
from logging_config import get_logger

router = APIRouter(prefix="/api/verdicts", tags=["verdicts"])
logger = get_logger(__name__)


class VerdictCreate(BaseModel):
    deliberation_id: str
    chosen_agent: Optional[str] = None
    user_override: Optional[str] = None
    verdict_type: str = "accepted"


@router.post("", status_code=201)
async def submit_verdict(body: VerdictCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    try:
        deliberation_uuid = UUID(body.deliberation_id)
    except (ValueError, AttributeError, TypeError) as e:
        # A malformed id is the caller's mistake, not a server fault.
        raise HTTPException(
            status_code=400, detail="deliberation_id must be a UUID"
        ) from e

    result = await db.execute(
        select(Deliberation).where(Deliberation.id == deliberation_uuid)
    )
    deliberation = result.scalar_one_or_none()
    if not deliberation:
        raise HTTPException(status_code=404, detail="Deliberation not found")

    chosen_response_id = None
    if body.chosen_agent:
        resp_result = await db.execute(
            select(AgentResponse)
            .where(AgentResponse.deliberation_id == body.deliberation_id)
            .where(AgentResponse.agent_name == body.chosen_agent)
            .limit(1)
        )
        agent_resp = resp_result.scalar_one_or_none()
        if agent_resp:
            chosen_response_id = agent_resp.id

    verdict = Verdict(
        id=str(uuid4()),
        deliberation_id=UUID(body.deliberation_id),
        chosen_response_id=chosen_response_id,
        chosen_agent=body.chosen_agent,
        user_override=body.user_override,
        verdict_type=body.verdict_type,
    )
    db.add(verdict)

    deliberation.status = "completed"

    # Collect everything the OKF filing needs *before* committing, so a failure
    # here can never 500 a verdict that was already accepted by the user.
    query = deliberation.user_query or ""
    agent_rows = (await db.execute(
        select(AgentResponse).where(AgentResponse.deliberation_id == body.deliberation_id)
    )).scalars().all()
    agent_summaries = {
        r.agent_name: str((r.response_json or {}).get("verdict", ""))
        or str((r.response_json or {}).get("answer", ""))
        or str((r.response_json or {}).get("consensus", ""))[:500]
        for r in agent_rows
    }

    await db.commit()
    await db.refresh(verdict)

    verdict_text = body.user_override or f"{body.verdict_type} (chosen: {body.chosen_agent or 'none'})"

    async def _file_okf():
        try:
            from okf.bundle import file_deliberation, BUNDLE_ROOT
            from okf.index import regenerate_indexes
            res = file_deliberation(
                deliberation_id=body.deliberation_id,
                query=query,
                verdict=verdict_text,
                agent_summaries=agent_summaries,
            )
            await regenerate_indexes(BUNDLE_ROOT)
            logger.info("okf_deliberation_filed", deliberation_id=body.deliberation_id, result=res)
        except Exception as e:
            logger.warning("okf_filing_failed", error=str(e))

    background_tasks.add_task(_file_okf)

    logger.info("verdict_submitted", deliberation_id=body.deliberation_id, verdict_type=body.verdict_type)
    return {
        "id": str(verdict.id),
        "deliberation_id": body.deliberation_id,
        "verdict_type": verdict.verdict_type,
        "status": "saved",
    }


@router.get("/{deliberation_id}")
async def get_verdict(deliberation_id: str, db: AsyncSession = Depends(get_db)):
    try:
        # The POST validates this and the GET did not, so a malformed id was a
        # 500 on one route and a 400 on the other. Same input, same answer.
        deliberation_uuid = UUID(deliberation_id)
    except (ValueError, AttributeError, TypeError) as e:
        raise HTTPException(
            status_code=400, detail="deliberation_id must be a UUID"
        ) from e

    result = await db.execute(
        select(Verdict).where(Verdict.deliberation_id == deliberation_uuid)
    )
    verdict = result.scalar_one_or_none()
    if not verdict:
        raise HTTPException(status_code=404, detail="No verdict found for this deliberation")

    return {
        "id": str(verdict.id),
        "deliberation_id": str(verdict.deliberation_id),
        "chosen_response_id": str(verdict.chosen_response_id) if verdict.chosen_response_id else None,
        "chosen_agent": verdict.chosen_agent,
        "user_override": verdict.user_override,
        "verdict_type": verdict.verdict_type,
        "created_at": str(verdict.created_at),
    }
