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
import threading
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _ConceptIndex:
    """One complete build of the concept index.

    Frozen and never mutated after publication, so a reader that captures the
    reference gets a coherent snapshot. The previous shape was a module-level
    dict updated key-by-key, which let a reader observe `ids` from one build
    next to `embeddings` from another — pairing the wrong rows with the wrong
    vectors and returning confidently wrong results rather than failing.
    """

    root: Path
    fingerprint: float
    ids: list[str]
    concepts: list[dict[str, Any]]
    embeddings: np.ndarray


#: The published snapshot. Rebound atomically, never mutated in place.
_concept_index: _ConceptIndex | None = None

#: Serialises the check-and-build. Callers reach this through
#: `asyncio.to_thread`, so several OS threads can arrive at once — at the start
#: of a debate all five characters retrieve simultaneously against a cold cache.
_index_lock = threading.Lock()


def invalidate_concept_cache(bundle_root: Path | None = None) -> None:
    """Drop the cached concept index.

    Must be called after anything writes into the bundle (e.g. filing a
    deliberation), otherwise the new concepts are invisible to semantic
    retrieval until the process restarts — which would defeat the whole
    point of the bundle compounding.
    """
    global _concept_index
    with _index_lock:
        index = _concept_index
        if (
            bundle_root is not None
            and index is not None
            and index.root != Path(bundle_root).resolve()
        ):
            return
        _concept_index = None


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


def _ensure_index(bundle_root: Path) -> _ConceptIndex:
    """Return the index for `bundle_root`, rebuilding it if the bundle moved.

    Double-checked: the fast path avoids the lock entirely, and the second
    check catches the case where another thread built it while this one waited.
    Only one thread ever embeds, so a cold cache costs one pass instead of one
    per concurrent caller.
    """
    global _concept_index
    root = Path(bundle_root).resolve()

    index = _concept_index
    if index is not None and index.root == root and index.fingerprint == _bundle_fingerprint(root):
        return index

    with _index_lock:
        # Recompute under the lock: the bundle can change between the fast-path
        # check and acquiring it, and publishing an index built from the older
        # fingerprint would leave the new concept invisible until the next call.
        fingerprint = _bundle_fingerprint(root)
        index = _concept_index
        if index is not None and index.root == root and index.fingerprint == fingerprint:
            return index

        concepts = list_concepts(root)
        # Through the shared embedder so the user's selection applies here too.
        from llm.embeddings import embed_sync

        texts = [_concept_text(c) for c in concepts] or ["empty"]
        emb = embed_sync(texts)
        built = _ConceptIndex(
            root=root,
            fingerprint=fingerprint,
            ids=[c["id"] for c in concepts],
            concepts=concepts,
            embeddings=np.asarray(emb, dtype=np.float32),
        )
        _concept_index = built
        return built


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
        # The returned snapshot, not the module global: another thread may
        # republish the index between here and the read below.
        index = _ensure_index(root)
    except Exception:
        return _keyword_retrieve(query, root, top_k)

    if index.embeddings is None or len(index.ids) == 0:
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

    scores = index.embeddings @ q_emb
    order = np.argsort(-scores)[:top_k]
    concepts = index.concepts
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
