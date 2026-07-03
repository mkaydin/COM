"""Sage Wiki API endpoints — ingest sources, lint wiki, view pages."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

from logging_config import get_logger

router = APIRouter(prefix="/api/wiki", tags=["wiki"])
logger = get_logger(__name__)

WIKI_ROOT = Path(__file__).parent.parent.parent / "sage_wiki"
WIKI_DIR = WIKI_ROOT / "wiki"


class WikiIngestRequest(BaseModel):
    source_path: str
    title: str


@router.get("/index")
async def get_wiki_index():
    """Get the wiki index.md content."""
    index_path = WIKI_DIR / "index.md"
    if index_path.exists():
        return {"content": index_path.read_text(), "path": "wiki/index.md"}
    return {"content": "", "path": "wiki/index.md"}


@router.get("/log")
async def get_wiki_log():
    """Get the wiki log.md content (last 100 entries)."""
    log_path = WIKI_DIR / "log.md"
    if log_path.exists():
        content = log_path.read_text()
        lines = content.split("\n")
        return {"content": "\n".join(lines[-200:]), "total_lines": len(lines)}
    return {"content": "", "total_lines": 0}


@router.get("/pages")
async def list_wiki_pages():
    """List all wiki pages with metadata."""
    pages = []
    if not WIKI_DIR.exists():
        return {"pages": [], "total": 0}

    for md_file in WIKI_DIR.rglob("*.md"):
        if md_file.name in ("index.md", "log.md"):
            continue
        rel = str(md_file.relative_to(WIKI_DIR))
        content = md_file.read_text()
        title = rel.replace("-", " ").replace(".md", "").title()
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                for line in content[3:end].strip().split("\n"):
                    if line.strip().startswith("title:"):
                        title = line.split(":", 1)[1].strip().strip('"')

        pages.append({
            "path": f"wiki/{rel}",
            "title": title,
            "size": len(content),
            "tags": [],
        })

    return {"pages": pages, "total": len(pages)}


@router.get("/page")
async def get_wiki_page(path: str):
    """Get a specific wiki page by path."""
    full_path = WIKI_DIR / path
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="Wiki page not found")
    return {"path": path, "content": full_path.read_text()}


@router.post("/ingest")
async def ingest_to_wiki(body: WikiIngestRequest):
    """Ingest a source file into the Sage wiki."""
    from agents.sage_wiki import ingest_source
    result = await ingest_source(body.source_path, body.title)
    return result


@router.post("/lint")
async def lint_wiki():
    """Run wiki health check."""
    from agents.sage_wiki import lint_wiki as _lint
    result = await _lint()
    return result
