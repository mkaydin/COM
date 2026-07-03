"""
Document parser — handles PDF, TXT, MD, CSV, JSON, and URL sources.
Uses LangChain document loaders where available, falls back to plain text.
"""
from pathlib import Path
from typing import Optional
from logging_config import get_logger

logger = get_logger(__name__)


def parse_file(file_path: str) -> list[dict]:
    """
    Parse a file into a list of text chunks with metadata.
    Returns [{"text": "...", "source": "filename", "page": 1}, ...]
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning("file_not_found", path=file_path)
        return []

    ext = path.suffix.lower()

    if ext == ".pdf":
        return _parse_pdf(path)
    elif ext == ".md":
        return _parse_markdown(path)
    elif ext in (".txt", ".csv", ".json", ".log"):
        return _parse_text(path)
    else:
        return _parse_text(path)


def parse_text_content(text: str, title: str = "Untitled") -> list[dict]:
    """Parse raw text content into chunks."""
    return [{"text": text, "source": title, "page": 1}]


def _parse_pdf(path: Path) -> list[dict]:
    try:
        from langchain_community.document_loaders import PyPDFLoader
        loader = PyPDFLoader(str(path))
        pages = loader.load()
        return [{"text": p.page_content, "source": path.name, "page": i + 1} for i, p in enumerate(pages)]
    except ImportError:
        logger.warning("pypdf_not_installed", hint="pip install pypdf")
        return _parse_text(path)
    except Exception as e:
        logger.error("pdf_parse_failed", error=str(e))
        return []


def _parse_markdown(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [{"text": text, "source": path.name, "page": 1}]


def _parse_text(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [{"text": text, "source": path.name, "page": 1}]
