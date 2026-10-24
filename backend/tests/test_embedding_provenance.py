"""Which embedder produced each stored vector.

Vectors from two different models are not comparable — cosine distance between
them is meaningless, not merely imprecise. Before `rag_chunks.embedding_model`
existed, changing `EMBEDDING_MODEL` left the old vectors in place with nothing
recording what made them, so retrieval degraded silently and no check could
detect it.

These cover the three parts: the identity string, the stamp written at
ingestion, and the mismatch report that tells you to run `tools/reembed.py`.
"""

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text

from llm import embeddings

pytestmark = pytest.mark.live

SESSION_LOOP = pytest.mark.asyncio(loop_scope="session")
BACKEND = Path(__file__).resolve().parents[1]


class TestIdentity:
    def test_identity_is_backend_and_model(self, monkeypatch):
        from config import get_settings

        monkeypatch.setattr(get_settings(), "embedding_backend", "ollama", raising=False)
        monkeypatch.setattr(
            get_settings(), "embedding_model", "qwen3-embedding:0.6b", raising=False
        )
        assert embeddings.identity() == "ollama:qwen3-embedding:0.6b"

    def test_identity_changes_with_the_selection(self, monkeypatch):
        from config import get_settings

        monkeypatch.setattr(get_settings(), "embedding_backend", "local", raising=False)
        monkeypatch.setattr(
            get_settings(), "embedding_model", "all-MiniLM-L6-v2", raising=False
        )
        assert embeddings.identity() == "local:all-MiniLM-L6-v2"


def _embedder_works() -> bool:
    from llm.embeddings import embed_sync

    try:
        vectors = embed_sync(["provenance probe"])
        return bool(vectors) and len(vectors[0]) == embeddings.VECTOR_DIM
    except Exception:
        return False


@SESSION_LOOP
async def test_ingestion_stamps_the_embedding_model():
    from db.postgres import async_session, init_db
    from db.models import RagChunk
    from rag.ingest import ingest

    if not _embedder_works():
        pytest.skip("embedding backend unavailable")

    await init_db()
    marker = uuid.uuid4().hex[:8]
    title = f"Provenance {marker}"
    async with async_session() as db:
        result = await ingest(
            db,
            # Unique per run: `ingest` dedupes on content hash, so a fixed body
            # makes the second run of the suite report "skipped" and fail.
            content=f"A passage about provenance ({marker}), written for this test.",
            title=title,
        )
        assert result["status"] == "ingested"
        rows = (
            await db.execute(select(RagChunk).where(RagChunk.title == title))
        ).scalars().all()

    assert rows, "nothing was stored"
    assert all(r.embedding_model == embeddings.identity() for r in rows), (
        f"chunks stamped {[r.embedding_model for r in rows]}, "
        f"expected {embeddings.identity()}"
    )


@SESSION_LOOP
async def test_stats_reports_chunks_from_a_different_model():
    """A mismatch must be visible without reading the database by hand."""
    from db.postgres import async_session, init_db
    from db.models import RagChunk
    from api.rag import rag_stats

    await init_db()
    marker = f"stale-{uuid.uuid4().hex[:8]}"

    async with async_session() as db:
        db.add(RagChunk(
            id=uuid.uuid4(),
            chunk_text="A chunk embedded by some other model.",
            embedding=[0.01] * embeddings.VECTOR_DIM,
            embedding_model="some-other-backend:some-other-model",
            source_type="user_upload",
            title=marker,
            content_hash=marker,
        ))
        await db.commit()

    async with async_session() as db:
        stats = await rag_stats(db)

    info = stats["embedding"]
    assert info["stale"] >= 1, "a foreign vector was not reported as stale"
    assert info["active"] == embeddings.identity()
    assert "some-other-backend:some-other-model" in info["by_model"]
    assert "reembed" in (info["hint"] or ""), (
        "the hint must say what to do; a count alone leaves the operator stuck"
    )


@SESSION_LOOP
async def test_reembed_command_stamps_and_clears_the_mismatch():
    from db.postgres import async_session, init_db
    from db.models import RagChunk
    from api.rag import rag_stats

    if not _embedder_works():
        pytest.skip("embedding backend unavailable")

    await init_db()
    marker = f"reembed-{uuid.uuid4().hex[:8]}"
    async with async_session() as db:
        db.add(RagChunk(
            id=uuid.uuid4(),
            chunk_text="A passage that predates the provenance column.",
            embedding=[0.01] * embeddings.VECTOR_DIM,
            embedding_model=None,
            source_type="user_upload",
            title=marker,
            content_hash=marker,
        ))
        await db.commit()

    proc = subprocess.run(
        [sys.executable, str(BACKEND / "tools" / "reembed.py")],
        capture_output=True, text=True, timeout=300,
        env={**__import__("os").environ},
    )
    assert proc.returncode == 0, f"reembed failed:\n{proc.stderr[-800:]}"

    async with async_session() as db:
        rows = (
            await db.execute(select(RagChunk).where(RagChunk.title == marker))
        ).scalars().all()
        assert all(r.embedding_model == embeddings.identity() for r in rows), (
            "the command did not stamp the chunk it re-embedded"
        )

        dims = (
            await db.execute(
                text(
                    "SELECT DISTINCT vector_dims(embedding) FROM rag_chunks "
                    "WHERE title = :t"
                ),
                {"t": marker},
            )
        ).scalars().all()
        assert dims == [embeddings.VECTOR_DIM]

        stats = await rag_stats(db)
    assert stats["embedding"]["stale"] == 0, (
        "chunks are still reported stale after a full re-embed"
    )