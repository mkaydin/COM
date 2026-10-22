"""Ingested content must actually be retrievable.

`ingestion/embedder.py` used to report success while storing nothing that any
retrieval path could see. Four faults compounded:

* each chunk's embedding was computed and then never passed to the row, so
  the column stayed NULL;
* that column was `Vector(1536)` while the embedder emits `VECTOR_DIM` (384),
  so passing it would have failed anyway;
* the rows went to `documents`, which retrieval never queries — it reads
  `rag_chunks`;
* a second copy went to ChromaDB, which nothing reads.

None of the existing tests noticed, because they exercised chunking and the
HTTP shape rather than asking "can I find this again afterwards?". These do.
"""

import uuid

import pytest
from sqlalchemy import select, text

from llm.embeddings import VECTOR_DIM

pytestmark = pytest.mark.live

# `async_session`/`init_db` are imported inside the tests: importing the engine
# at module scope binds it to whatever loop pytest happened to be using.
SESSION_LOOP = pytest.mark.asyncio(loop_scope="session")


def _payload(marker: str) -> str:
    return (
        f"The marker for this passage is {marker}. "
        "Matryoshka representation learning keeps leading dimensions meaningful "
        "so a single embedding can serve several truncation widths. "
    ) * 20


@SESSION_LOOP
async def test_ingestion_stores_vectors_in_the_store_retrieval_reads():
    from db.postgres import async_session, init_db
    from db.models import RagChunk
    from ingestion.embedder import ingest_document

    await init_db()

    marker = f"ingest-probe-{uuid.uuid4().hex[:10]}"
    title = f"Ingestion contract {marker}"
    result = await ingest_document(
        str(uuid.uuid4()), title, _payload(marker), "user_upload",
    )
    assert result["status"] == "ingested", result
    assert result["chunks"] >= 1

    async with async_session() as db:
        rows = (
            await db.execute(select(RagChunk).where(RagChunk.title == title))
        ).scalars().all()

        assert rows, (
            "the document was reported ingested but no RagChunk rows exist — "
            "retrieval reads this table, so the content is unreachable"
        )

        missing = [r.id for r in rows if r.embedding is None]
        assert not missing, (
            f"{len(missing)} of {len(rows)} chunks have NULL embeddings; the "
            "vector was computed and dropped instead of stored"
        )

        dims = (
            await db.execute(
                text(
                    "SELECT DISTINCT vector_dims(embedding) FROM rag_chunks "
                    "WHERE title = :t"
                ),
                {"t": title},
            )
        ).scalars().all()
        assert dims == [VECTOR_DIM], (
            f"stored width {dims} does not match VECTOR_DIM={VECTOR_DIM}; a "
            "mismatch means the column and the embedder disagree"
        )


@SESSION_LOOP
async def test_ingested_content_comes_back_from_retrieval():
    """The end-to-end claim: save it, then find it by meaning."""
    from db.postgres import async_session, init_db
    from ingestion.embedder import ingest_document
    from rag.retrieval import retrieve

    await init_db()

    marker = f"retrieval-probe-{uuid.uuid4().hex[:10]}"
    title = f"Retrieval contract {marker}"
    await ingest_document(str(uuid.uuid4()), title, _payload(marker), "user_upload")

    async with async_session() as db:
        out = await retrieve(
            db, "why do nested embeddings keep their meaning when truncated", top_k=10
        )

    raw = out.get("raw") or []
    # Retrieval normalises each hit into `text`; `chunk_text` is the column name
    # and is not what comes back, which is easy to assert on and never notice.
    hit = [r for r in raw if marker in (r.get("text") or "")]
    assert hit, (
        "the ingested passage did not come back from retrieval — it is stored "
        f"but unreachable. {len(raw)} raw results returned: "
        f"{[r.get('title') for r in raw][:4]}"
    )


@SESSION_LOOP
async def test_reingesting_the_same_content_is_deduped():
    """`rag.ingest` owns dedupe; delegating must not lose it."""
    from ingestion.embedder import ingest_document

    marker = f"dedupe-probe-{uuid.uuid4().hex[:10]}"
    title = f"Dedupe contract {marker}"
    body = _payload(marker)

    first = await ingest_document(str(uuid.uuid4()), title, body, "user_upload")
    second = await ingest_document(str(uuid.uuid4()), title, body, "user_upload")

    assert first["status"] == "ingested"
    assert second["status"] == "skipped", (
        "the same content was stored twice; dedupe lives in rag.ingest and "
        "must survive the delegation"
    )