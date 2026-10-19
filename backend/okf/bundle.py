"""High-level OKF bundle operations for the Oracle agent.

Wraps the vendored OKF core with three capabilities the Oracle needs:
  1. list_concepts()       — enumerate the bundle's knowledge.
  2. retrieve_relevant()   — semantic top-k retrieval (sentence-transformers).
  3. file_deliberation()   — persist a finished deliberation as a concept.

The bundle lives on disk at bundles/oracle/ and is a plain directory tree of
OKF markdown — browsable by humans, agents, git, and the OKF viewer.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from okf.document import OKFDocument
from okf.context import set_context
from okf.bundle_tools import write_concept
from okf.paths import parse_concept_id

BUNDLE_ROOT = Path(__file__).parent / "bundles" / "oracle"

_INDEX_NAME = "index.md"
_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    s = _slug_re.sub("_", (text or "").lower()).strip("_")
    return s[:80] or "untitled"


def list_concepts(bundle_root: Path | None = None) -> list[dict[str, Any]]:
    """Walk the bundle and return every concept's metadata.

    Index files are excluded. Each entry: {id, type, title, description,
    resource, tags, path, body_chars}.
    """
    root = Path(bundle_root or BUNDLE_ROOT)
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for md_path in sorted(root.rglob("*.md")):
        if md_path.name == _INDEX_NAME:
            continue
        rel = md_path.relative_to(root).with_suffix("")
        concept_id = "/".join(rel.parts)
        try:
            doc = OKFDocument.parse(md_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        fm = doc.frontmatter or {}
        tags = fm.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        out.append({
            "id": concept_id,
            "type": str(fm.get("type") or "Unknown"),
            "title": str(fm.get("title") or concept_id),
            "description": str(fm.get("description") or ""),
            "resource": str(fm.get("resource") or ""),
            "tags": [str(t) for t in tags],
            "path": str(rel),
            "body_chars": len(doc.body or ""),
        })
    return out


# --- semantic retrieval (sentence-transformers, cached) ----------------------

_st_model = None
_concept_cache: dict[str, Any] = {"root": None, "ids": [], "embeddings": None, "mtime": None}


def invalidate_concept_cache(bundle_root: Path | None = None) -> None:
    """Drop the cached concept index.

    Must be called after anything writes into the bundle (e.g. filing a
    deliberation), otherwise the new concepts are invisible to semantic
    retrieval until the process restarts — which would defeat the whole
    point of the bundle compounding.
    """
    if bundle_root is not None and _concept_cache["root"] not in (None, Path(bundle_root).resolve()):
        return
    _concept_cache.update(root=None, ids=[], embeddings=None, mtime=None)


def _bundle_fingerprint(root: Path) -> float:
    """Cheap change detector: newest mtime across the bundle tree."""
    newest = 0.0
    for p in root.rglob("*.md"):
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    return newest


def _get_st_model():
    """Deprecated. Kept only so a stale import fails loudly rather than
    silently returning a model from the wrong backend.

    This used to hold a second hardcoded `SentenceTransformer("all-MiniLM-L6-v2")`,
    so the same weights were resident twice. `llm.embeddings` owns the single
    loaded instance now; call `llm.embeddings.embed_sync` instead.
    """
    from llm.embeddings import _get_local_model

    return _get_local_model()


def _concept_text(c: dict[str, Any]) -> str:
    parts = [c["title"], c["description"]]
    parts.extend(c.get("tags", []))
    return " ".join(p for p in parts if p)


def _ensure_index(bundle_root: Path) -> None:
    root = Path(bundle_root).resolve()
    fingerprint = _bundle_fingerprint(root)
    if (
        _concept_cache["root"] == root
        and _concept_cache["embeddings"] is not None
        and _concept_cache["mtime"] == fingerprint
    ):
        return
    concepts = list_concepts(root)
    # Through the shared embedder so the user's selection applies here too.
    from llm.embeddings import embed_sync

    texts = [_concept_text(c) for c in concepts] or ["empty"]
    emb = embed_sync(texts)
    _concept_cache.update(
        root=root,
        ids=[c["id"] for c in concepts],
        embeddings=np.asarray(emb, dtype=np.float32),
        concepts=concepts,
        mtime=fingerprint,
    )


def retrieve_relevant(
    query: str,
    bundle_root: Path | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Return the top_k concepts most semantically similar to `query`.

    Uses cosine similarity over sentence-transformer embeddings of
    (title + description + tags). Falls back to keyword overlap if the
    embedding model is unavailable.
    """
    root = Path(bundle_root or BUNDLE_ROOT)
    try:
        _ensure_index(root)
    except Exception:
        return _keyword_retrieve(query, root, top_k)

    cache = _concept_cache
    if cache["embeddings"] is None or len(cache["ids"]) == 0:
        return []

    # The query must come from the same backend that built the index above, or
    # the two vectors are not comparable. Reaching past `llm.embeddings` for
    # the raw sentence-transformer did exactly that whenever a non-local
    # backend was selected.
    try:
        from llm.embeddings import embed_sync

        q_emb = np.asarray(embed_sync([query])[0], dtype=np.float32)
    except Exception:
        return _keyword_retrieve(query, root, top_k)

    scores = cache["embeddings"] @ q_emb
    order = np.argsort(-scores)[:top_k]
    concepts = cache["concepts"]
    out = []
    for i in order:
        c = dict(concepts[i])
        c["score"] = float(scores[i])
        out.append(c)
    return out


def _keyword_retrieve(query: str, root: Path, top_k: int) -> list[dict[str, Any]]:
    concepts = list_concepts(root)
    qtokens = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2}
    scored = []
    for c in concepts:
        text = (c["title"] + " " + c["description"] + " " + " ".join(c["tags"])).lower()
        toks = set(re.findall(r"[a-z0-9]+", text))
        score = len(qtokens & toks)
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    result = []
    for score, c in scored[:top_k]:
        c = dict(c)
        c["score"] = float(score)
        result.append(c)
    return result


def load_concept_body(concept_id: str, bundle_root: Path | None = None) -> str:
    """Load the full markdown body of a concept by id."""
    root = Path(bundle_root or BUNDLE_ROOT)
    cid = parse_concept_id(concept_id)
    path = root.joinpath(*cid[:-1], f"{cid[-1]}.md") if len(cid) > 1 else root / f"{cid[0]}.md"
    if not path.exists():
        return ""
    try:
        doc = OKFDocument.parse(path.read_text(encoding="utf-8"))
        return doc.body or ""
    except Exception:
        return ""


def format_context(concepts: list[dict[str, Any]], bundle_root: Path | None = None) -> str:
    """Format retrieved concepts into an LLM context block."""
    if not concepts:
        return "(The Oracle's knowledge bundle is empty — no prior deliberations or references filed yet.)"
    parts = []
    for c in concepts:
        body = load_concept_body(c["id"], bundle_root)
        if len(body) > 2000:
            body = body[:2000] + "\n...[truncated]"
        parts.append(
            f"--- Concept: {c['id']} (type: {c['type']}) ---\n"
            f"Title: {c['title']}\n"
            f"Description: {c['description']}\n"
            f"Body:\n{body}"
        )
    return "\n\n".join(parts)


def file_deliberation(
    *,
    deliberation_id: str,
    query: str,
    verdict: str,
    agent_summaries: dict[str, str] | None = None,
    tags: list[str] | None = None,
    bundle_root: Path | None = None,
) -> dict[str, Any]:
    """Persist a finished deliberation as an OKF concept in the bundle.

    Writes deliberations/<slug>.md and returns the write_concept result dict.
    Safe to call as a background task after a verdict is filed.
    """
    root = Path(bundle_root or BUNDLE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    set_context(root)

    slug = slugify(query)
    concept_id = f"deliberations/{slug}"

    body_parts = [f"# {query}", ""]
    if agent_summaries:
        for agent, summary in agent_summaries.items():
            body_parts.append(f"## {agent.title()}")
            body_parts.append(summary or "(no output)")
            body_parts.append("")
    body_parts.append("## Verdict")
    body_parts.append(verdict or "(no verdict filed)")
    body_parts.append("")
    body_parts.append("# Citations")
    body_parts.append(f"[1] Council Deliberation {deliberation_id}")

    frontmatter = {
        "type": "Deliberation",
        "title": query[:200],
        "description": (query[:200] + "…" if len(query) > 200 else query),
        "resource": f"council://deliberation/{deliberation_id}",
        "tags": list(tags or [])[:10],
    }

    return write_concept(concept_id, frontmatter, "\n".join(body_parts))


def file_reference(
    *,
    title: str,
    description: str,
    body: str,
    resource: str = "",
    tags: list[str] | None = None,
    bundle_root: Path | None = None,
) -> dict[str, Any]:
    """Persist a cross-cutting reference concept (e.g. a library doc)."""
    root = Path(bundle_root or BUNDLE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    set_context(root)

    slug = slugify(title)
    concept_id = f"references/{slug}"
    frontmatter = {
        "type": "Reference",
        "title": title[:200],
        "description": description,
        "resource": resource,
        "tags": list(tags or [])[:10],
    }
    return write_concept(concept_id, frontmatter, body)
