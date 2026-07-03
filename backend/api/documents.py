from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID, uuid4
from datetime import datetime

from db.postgres import get_db
from db.models import Document
from logging_config import get_logger

router = APIRouter(prefix="/api/documents", tags=["documents"])
logger = get_logger(__name__)


class DocumentResponse(BaseModel):
    id: UUID
    title: str
    source_type: Optional[str]
    created_at: datetime
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
    query = select(Document).where(~Document.source_type.endswith('_chunk')).order_by(Document.created_at.desc()).limit(limit)
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


@router.get("/{document_id}")
async def get_document(document_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return {
        "id": str(doc.id),
        "title": doc.title,
        "content": doc.content,
        "source_type": doc.source_type,
        "created_at": str(doc.created_at),
        "metadata": doc.metadata_json,
    }


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
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """Upload a file for ingestion. Triggers RAG + GraphRAG pipelines in background."""
    import tempfile, os

    raw = await file.read()
    filename = file.filename or "Untitled"
    ext = os.path.splitext(filename)[1].lower()

    text = ""
    if ext == ".pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        try:
            from ingestion.parser import _parse_pdf
            from pathlib import Path
            pages = _parse_pdf(Path(tmp_path))
            text = "\n\n".join(p["text"] for p in pages) if pages else ""
            if not text:
                text = raw.decode("utf-8", errors="replace")
        finally:
            os.unlink(tmp_path)
    else:
        text = raw.decode("utf-8", errors="replace")

    text = text[:100000]
    text = text.replace("\x00", "").replace("\ufffd", "")

    doc = Document(
        id=uuid4(),
        title=filename,
        content=text,
        source_type="user_upload",
        file_path=None,
        metadata_json={"size_bytes": len(raw), "content_type": file.content_type, "pages": ext == ".pdf", "ingestion_status": "pending"},
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    logger.info("document_uploaded", document_id=str(doc.id), title=doc.title, chars=len(text))

    background_tasks.add_task(
        _run_ingestion,
        str(doc.id),
        doc.title,
        text,
    )

    return {"id": str(doc.id), "title": doc.title, "status": "uploaded", "chars": len(text), "ingestion": "processing in background"}


async def _run_ingestion(doc_id: str, title: str, text: str):
    """Run RAG + GraphRAG ingestion in background."""
    try:
        from ingestion.embedder import ingest_document
        await ingest_document(doc_id, title, text, "user_upload")
        logger.info("rag_ingestion_complete", doc_id=doc_id)
    except Exception as e:
        logger.error("rag_ingestion_failed", doc_id=doc_id, error=str(e))

    try:
        from ingestion.graph_extractor import ingest_to_graph
        await ingest_to_graph(doc_id, title, text[:4000])
        logger.info("graph_ingestion_complete", doc_id=doc_id)
    except Exception as e:
        logger.error("graph_ingestion_failed", doc_id=doc_id, error=str(e))


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
