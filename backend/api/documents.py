from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID, uuid4

from db.postgres import get_db
from db.models import Document
from logging_config import get_logger

router = APIRouter(prefix="/api/documents", tags=["documents"])
logger = get_logger(__name__)


class DocumentResponse(BaseModel):
    id: UUID
    title: str
    source_type: Optional[str]
    created_at: str
    model_config = {"from_attributes": True}


class ScoutReportSave(BaseModel):
    deliberation_id: Optional[str] = Field(default=None)
    topic: str
    summarized_text: str
    original_urls: List[str] = Field(default_factory=list)
    original_texts: Optional[str] = Field(default=None)
    confidence: Optional[float] = Field(default=None)
    key_signals: Optional[str] = Field(default=None)


@router.get("", response_model=List[DocumentResponse])
async def list_documents(
    source_type: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    query = select(Document).order_by(Document.created_at.desc()).limit(limit)
    if source_type:
        query = query.where(Document.source_type == source_type)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/search")
async def search_documents(q: str = "", db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document)
        .where(Document.title.ilike(f"%{q}%") | Document.content.ilike(f"%{q}%"))
        .order_by(Document.created_at.desc())
        .limit(20)
    )
    return result.scalars().all()


@router.delete("/{document_id}", status_code=204)
async def delete_document(document_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    await db.delete(doc)
    await db.commit()
    logger.info("document_deleted", document_id=str(document_id))


@router.post("/upload", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """Upload a file for RAG ingestion. Supports multiple files via repeated 'file' fields."""
    content = await file.read()
    text = content.decode("utf-8", errors="replace")

    doc = Document(
        id=uuid4(),
        title=file.filename or "Untitled",
        content=text[:100000],
        source_type="user_upload",
        file_path=None,
        metadata_json={"size_bytes": len(content), "content_type": file.content_type},
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    logger.info("document_uploaded", document_id=str(doc.id), title=doc.title)
    return {"id": str(doc.id), "title": doc.title, "status": "uploaded"}


@router.post("/save-scout-report", status_code=201)
async def save_scout_report(body: ScoutReportSave, db: AsyncSession = Depends(get_db)):
    """Save a Scout intelligence report to the Council Library.

    This is a user-initiated action - only saves when explicitly requested.
    Stores both the summarized intelligence and the original source texts/URLs.
    """

    original_section = ""
    if body.original_urls:
        original_section += "\n\n## Original Sources\n"
        for url in body.original_urls:
            original_section += f"- {url}\n"
    if body.original_texts:
        original_section += f"\n\n## Original Texts\n{body.original_texts}"

    full_content = f"""# Scout Report: {body.topic}

## Summary
{body.summarized_text}

## Key Signals
{body.key_signals or 'None recorded'}

## Confidence
{body.confidence or 'N/A'}
{original_section}
"""

    metadata = {
        "deliberation_id": body.deliberation_id,
        "topic": body.topic,
        "confidence": body.confidence,
        "source_type": "scout_report",
        "source_urls": body.original_urls,
    }

    doc = Document(
        id=uuid4(),
        title=f"[Scout] {body.topic[:200]}",
        content=full_content,
        source_type="scout_report",
        file_path=None,
        metadata_json=metadata,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    logger.info("scout_report_saved", document_id=str(doc.id), topic=body.topic[:80])
    return {"id": str(doc.id), "title": doc.title, "status": "saved"}
