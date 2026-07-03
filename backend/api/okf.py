"""OKF (Open Knowledge Format) API — browse, enrich, and visualize the Oracle's knowledge bundle."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from typing import Optional, List
from logging_config import get_logger

router = APIRouter(prefix="/api/okf", tags=["okf"])
logger = get_logger(__name__)


@router.get("/concepts")
async def list_concepts():
    """List every concept in the Oracle's OKF bundle (progressive-disclosure metadata)."""
    from okf.bundle import list_concepts as _list, BUNDLE_ROOT
    return {"bundle": str(BUNDLE_ROOT), "concepts": _list(BUNDLE_ROOT)}


@router.get("/concepts/{concept_id:path}")
async def get_concept(concept_id: str):
    """Read a single concept's full frontmatter + body."""
    from okf.bundle import BUNDLE_ROOT
    from okf.paths import parse_concept_id
    from okf.document import OKFDocument
    cid = parse_concept_id(concept_id)
    path = BUNDLE_ROOT.joinpath(*cid[:-1], f"{cid[-1]}.md") if len(cid) > 1 else BUNDLE_ROOT / f"{cid[0]}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Concept not found: {concept_id}")
    doc = OKFDocument.parse(path.read_text(encoding="utf-8"))
    return {
        "id": concept_id,
        "frontmatter": doc.frontmatter,
        "body": doc.body,
        "path": str(path.relative_to(BUNDLE_ROOT)),
    }


class RetrieveRequest(BaseModel):
    query: str
    top_k: int = 5


@router.post("/retrieve")
async def retrieve(req: RetrieveRequest):
    """Semantic top-k retrieval over the bundle for a query."""
    from okf.bundle import retrieve_relevant, BUNDLE_ROOT
    import asyncio
    results = await asyncio.to_thread(retrieve_relevant, req.query, BUNDLE_ROOT, req.top_k)
    return {"query": req.query, "results": results}


@router.post("/regenerate-indexes")
async def regenerate_indexes():
    """Regenerate all index.md files in the bundle."""
    from okf.bundle import BUNDLE_ROOT
    from okf.index import regenerate_indexes as _regen
    written = await _regen(BUNDLE_ROOT)
    return {"written": [str(p.relative_to(BUNDLE_ROOT)) for p in written], "count": len(written)}


@router.get("/viz", response_class=HTMLResponse)
async def visualize():
    """Generate (fresh on each call) the self-contained HTML graph view of the bundle."""
    import tempfile, os
    from okf.bundle import BUNDLE_ROOT
    from okf.viewer import generate_visualization
    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
    tmp.close()
    stats = generate_visualization(BUNDLE_ROOT, __import__("pathlib").Path(tmp.name), bundle_name="Oracle Knowledge")
    try:
        html = open(tmp.name, encoding="utf-8").read()
    finally:
        os.unlink(tmp.name)
    logger.info("okf_viz_generated", concepts=stats["concepts"], edges=stats["edges"])
    return HTMLResponse(content=html)
