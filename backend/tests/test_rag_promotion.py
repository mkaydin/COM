"""Tests for the RAG promotion queue (spec §2.6, §4.5, §5)."""
import pytest


class TestPromotionQueue:
    @staticmethod
    def _make_engine():
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool
        from config import get_settings
        return create_async_engine(get_settings().postgres_url, poolclass=NullPool)

    @pytest.mark.asyncio
    async def test_create_and_list_candidate(self):
        from sqlalchemy.ext.asyncio import AsyncSession
        from rag.ingest import ingest
        from rag.promotion import create_candidate, list_pending
        from sqlalchemy import text

        engine = self._make_engine()
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(text("DELETE FROM rag_promotion_candidates WHERE 1=1"))
            await db.execute(text("DELETE FROM rag_chunks WHERE title LIKE 'TESTPROMO_%'"))
            await db.commit()

            # Ingest a chunk to promote.
            r = await ingest(db, content="# Test\n\nPromotion candidate content.\n",
                             title="TESTPROMO_doc", source_type="user_upload")
            assert r["status"] == "ingested"

            # Get the chunk id.
            chunk = (await db.execute(text(
                "SELECT id FROM rag_chunks WHERE title = 'TESTPROMO_doc' LIMIT 1"
            ))).fetchone()
            assert chunk is not None

            # Create candidate.
            cand = await create_candidate(db, chunk_id=str(chunk[0]), reason="manual",
                                          trigger_query="test query", summary="Summary text.")
            assert cand["status"] == "created"

            # List pending.
            pending = await list_pending(db)
            assert len(pending) >= 1
            assert pending[0]["chunk_title"] == "TESTPROMO_doc"
            assert pending[0]["reason"] == "manual"

            await db.execute(text("DELETE FROM rag_promotion_candidates WHERE 1=1"))
            await db.execute(text("DELETE FROM rag_chunks WHERE title LIKE 'TESTPROMO_%'"))
            await db.commit()
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_idempotent_create(self):
        """Creating a candidate for the same chunk twice returns 'exists'."""
        from sqlalchemy.ext.asyncio import AsyncSession
        from rag.ingest import ingest
        from rag.promotion import create_candidate
        from sqlalchemy import text

        engine = self._make_engine()
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(text("DELETE FROM rag_promotion_candidates WHERE 1=1"))
            await db.execute(text("DELETE FROM rag_chunks WHERE title LIKE 'TESTPROMO_IDEM_%'"))
            await db.commit()

            r = await ingest(db, content="Idempotent promotion test.\n",
                             title="TESTPROMO_IDEM_doc", source_type="user_upload")
            chunk = (await db.execute(text(
                "SELECT id FROM rag_chunks WHERE title = 'TESTPROMO_IDEM_doc' LIMIT 1"
            ))).fetchone()

            c1 = await create_candidate(db, chunk_id=str(chunk[0]), reason="manual")
            c2 = await create_candidate(db, chunk_id=str(chunk[0]), reason="manual")
            assert c1["status"] == "created"
            assert c2["status"] == "exists"

            await db.execute(text("DELETE FROM rag_promotion_candidates WHERE 1=1"))
            await db.execute(text("DELETE FROM rag_chunks WHERE title LIKE 'TESTPROMO_IDEM_%'"))
            await db.commit()
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_reject_candidate(self):
        from sqlalchemy.ext.asyncio import AsyncSession
        from rag.ingest import ingest
        from rag.promotion import create_candidate, reject
        from sqlalchemy import text

        engine = self._make_engine()
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(text("DELETE FROM rag_promotion_candidates WHERE 1=1"))
            await db.execute(text("DELETE FROM rag_chunks WHERE title LIKE 'TESTPROMO_REJ_%'"))
            await db.commit()

            await ingest(db, content="Reject test content.\n",
                         title="TESTPROMO_REJ_doc", source_type="user_upload")
            chunk = (await db.execute(text(
                "SELECT id FROM rag_chunks WHERE title = 'TESTPROMO_REJ_doc' LIMIT 1"
            ))).fetchone()

            cand = await create_candidate(db, chunk_id=str(chunk[0]), reason="manual")
            result = await reject(db, candidate_id=cand["candidate_id"])
            assert result["status"] == "rejected"

            # Verify no longer pending.
            from rag.promotion import list_pending
            pending = await list_pending(db)
            assert not any(p["candidate_id"] == cand["candidate_id"] for p in pending)

            await db.execute(text("DELETE FROM rag_promotion_candidates WHERE 1=1"))
            await db.execute(text("DELETE FROM rag_chunks WHERE title LIKE 'TESTPROMO_REJ_%'"))
            await db.commit()
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_approve_writes_okf_concept(self):
        """Spec §5: approve writes an OKF concept + marks chunk promoted."""
        from sqlalchemy.ext.asyncio import AsyncSession
        from rag.ingest import ingest
        from rag.promotion import create_candidate, approve
        from sqlalchemy import text

        engine = self._make_engine()
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(text("DELETE FROM rag_promotion_candidates WHERE 1=1"))
            await db.execute(text("DELETE FROM rag_chunks WHERE title LIKE 'TESTPROMO_APP_%'"))
            await db.commit()

            await ingest(db, content="# Methods\n\nApproval test for OKF promotion.\n",
                         title="TESTPROMO_APP_doc", source_type="user_upload",
                         trust_tier="peer_reviewed")
            chunk = (await db.execute(text(
                "SELECT id FROM rag_chunks WHERE title = 'TESTPROMO_APP_doc' LIMIT 1"
            ))).fetchone()

            cand = await create_candidate(db, chunk_id=str(chunk[0]), reason="manual",
                                          summary="Candidate summary for promotion.")

            result = await approve(db, candidate_id=cand["candidate_id"])
            assert result["status"] == "approved"
            assert "okf_concept_ref" in result
            assert result["okf_concept_ref"].startswith("references/")

            # Verify chunk is marked promoted.
            promoted = (await db.execute(text(
                "SELECT promoted_to_okf, okf_concept_ref FROM rag_chunks WHERE id = '{}'".format(chunk[0])
            ))).fetchone()
            assert promoted[0] == True
            assert promoted[1] is not None

            # Verify candidate is approved.
            cand_status = (await db.execute(text(
                "SELECT status FROM rag_promotion_candidates WHERE id = '{}'".format(
                    cand["candidate_id"])
            ))).fetchone()
            assert cand_status[0] == "approved"

            # Cleanup: remove the OKF concept file + DB records.
            import os
            from okf.bundle import BUNDLE_ROOT
            okf_path = BUNDLE_ROOT / result["okf_concept_ref"]
            if okf_path.exists():
                os.unlink(okf_path)

            await db.execute(text("DELETE FROM rag_promotion_candidates WHERE 1=1"))
            await db.execute(text("DELETE FROM rag_chunks WHERE title LIKE 'TESTPROMO_APP_%'"))
            await db.commit()
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_flag_gap_candidates(self):
        """Spec §4.5: gap detection auto-flags top chunks for promotion."""
        from sqlalchemy.ext.asyncio import AsyncSession
        from rag.ingest import ingest
        from rag.promotion import flag_gap_candidates, list_pending
        from sqlalchemy import text

        engine = self._make_engine()
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(text("DELETE FROM rag_promotion_candidates WHERE 1=1"))
            await db.execute(text("DELETE FROM rag_chunks WHERE title LIKE 'TESTPROMO_GAP_%'"))
            await db.commit()

            # Ingest 3 chunks.
            chunk_ids = []
            for i in range(3):
                await ingest(db, content=f"Gap detection test content {i}.\n",
                             title=f"TESTPROMO_GAP_{i}", source_type="user_upload")
                row = (await db.execute(text(
                    f"SELECT id FROM rag_chunks WHERE title = 'TESTPROMO_GAP_{i}' LIMIT 1"
                ))).fetchone()
                chunk_ids.append(str(row[0]))

            flagged = await flag_gap_candidates(db, "gap test query", chunk_ids)
            assert flagged == 3

            pending = await list_pending(db)
            gap_cands = [p for p in pending if p["reason"] == "gap_detection"]
            assert len(gap_cands) >= 3

            await db.execute(text("DELETE FROM rag_promotion_candidates WHERE 1=1"))
            await db.execute(text("DELETE FROM rag_chunks WHERE title LIKE 'TESTPROMO_GAP_%'"))
            await db.commit()
        await engine.dispose()
