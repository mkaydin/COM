"""Filing a concluded topic into the library.

The document is ingested through the normal RAG pipeline, so a past debate
becomes knowledge later topics can retrieve. These tests use a real database but
no LLM: the posts are written directly, so the subject under test is the save
path rather than the debate.

`pytest -m live --run-live`.
"""

import uuid

import pytest

pytestmark = pytest.mark.live

SESSION_LOOP = pytest.mark.asyncio(loop_scope="session")


async def _concluded_topic():
    """A minimal concluded topic with one character and a revision."""
    from db.postgres import async_session
    from agents.forum_store import append_post, conclude_topic, create_topic

    marker = f"save-probe-{uuid.uuid4().hex[:10]}"
    async with async_session() as db:
        topic = await create_topic(
            db,
            f"Should we adopt exact search over a vector database? {marker}",
            title=f"Save contract {marker}",
            framing="Decide whether the operational cost is justified.",
            cast=["oracle"],
            max_rounds=1,
        )
        tid = uuid.UUID(topic["id"])

        await append_post(
            db, tid, "oracle",
            "Adopt a vector database now; exact search will not scale.",
            author_kind="character", kind="statement", stance="propose", round=0,
            commit=False,
        )
        await append_post(
            db, tid, "oracle",
            "Start with exact search in Postgres; the corpus is small and a "
            "second datastore costs more than the migration it avoids.",
            author_kind="character", kind="revision", stance="propose", round=1,
            commit=False,
        )
        await conclude_topic(
            db, tid,
            outcome="plurality",
            synthesis={
                "mode": "plurality",
                "answer": "",
                "positions": [
                    {"label": "Postgres first", "position": "Start exact.",
                     "backers": ["oracle"]}
                ],
                "fault_line": "Cost now versus migration later.",
                "dissent": [],
                "signals_note": "",
                "process": "The oracle moved after weighing the migration cost.",
                "changes": [{"author": "oracle", "reason": "migration cost"}],
            },
            signals={"converged": False, "cast": ["oracle"]},
            commit=False,
        )
        await db.commit()
    return tid


@SESSION_LOOP
async def test_saving_files_a_document_and_indexes_it():
    from db.postgres import async_session, init_db
    from db.models import Document
    from sqlalchemy import select, text
    from api.forum import save_topic_to_library

    await init_db()
    tid = await _concluded_topic()

    async with async_session() as db:
        result = await save_topic_to_library(tid, force=False, db=db)

    assert result["status"] == "saved", result
    assert result["chunks"] >= 1, "the document was filed but produced no chunks"
    assert result["trust_tier"] == "ai_summary", (
        "a debate is model output and must not rank as a primary source"
    )

    async with async_session() as db:
        doc = await db.get(Document, uuid.UUID(result["id"]))
        assert doc is not None and doc.source_type == "forum_topic"
        # Finals first: the document is meant to be readable, not just indexed.
        assert "## Final answers" in doc.content
        assert doc.content.index("## Final answers") < doc.content.index("## Discussion")

        chunks = (
            await db.execute(
                text(
                    "SELECT count(*) FROM rag_chunks "
                    "WHERE source_type = 'forum_topic' AND source_doc_id = :d"
                ),
                {"d": str(doc.id)},
            )
        ).scalar()
        assert chunks >= 1, "nothing reached the store retrieval reads"


@SESSION_LOOP
async def test_saving_twice_does_not_duplicate():
    from db.postgres import async_session, init_db
    from api.forum import save_topic_to_library

    await init_db()
    tid = await _concluded_topic()

    async with async_session() as db:
        first = await save_topic_to_library(tid, force=False, db=db)
    async with async_session() as db:
        second = await save_topic_to_library(tid, force=False, db=db)

    assert first["status"] == "saved"
    assert second["status"] == "already_saved"
    assert second["id"] == first["id"]


@SESSION_LOOP
async def test_an_open_topic_cannot_be_filed():
    """No finals yet — filing it would store a half-finished debate."""
    from fastapi import HTTPException
    from db.postgres import async_session, init_db
    from agents.forum_store import create_topic
    from api.forum import save_topic_to_library

    await init_db()
    async with async_session() as db:
        topic = await create_topic(
            db, f"Still running {uuid.uuid4().hex[:8]}", cast=["oracle"]
        )
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await save_topic_to_library(uuid.UUID(topic["id"]), force=False, db=db)
    assert exc.value.status_code == 409
    assert "concluded" in str(exc.value.detail)