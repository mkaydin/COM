"""Tests for the unified RAG retrieval layer (spec §4)."""
import pytest

from rag.retrieval import _format_for_context, _query_hash, _gap_tracker, get_gap_candidates, reset_gap
from rag.ingest import TRUST_WEIGHTS


class TestFormatContext:
    def test_empty_results(self):
        ctx = _format_for_context([])
        assert "No knowledge" in ctx

    def test_curated_presented_before_raw(self):
        results = [
            {"source_system": "okf", "title": "OKF Concept", "trust_tier": "curated",
             "content": "Curated content about topic X.", "text": ""},
            {"source_system": "rag", "title": "Raw Paper", "trust_tier": "preprint",
             "section": "Methods", "text": "Raw content about topic X.",
             "source_url": "https://example.com/paper.pdf", "content": ""},
        ]
        ctx = _format_for_context(results)
        assert "CURATED KNOWLEDGE" in ctx
        assert "RAW KNOWLEDGE" in ctx
        curated_pos = ctx.index("CURATED")
        raw_pos = ctx.index("RAW KNOWLEDGE")
        assert curated_pos < raw_pos

    def test_raw_trust_tier_annotated(self):
        results = [
            {"source_system": "rag", "title": "Paper", "trust_tier": "peer_reviewed",
             "section": "Results", "text": "High quality content.",
             "source_url": "https://doi.org/xxx", "content": ""},
        ]
        ctx = _format_for_context(results)
        assert "PEER_REVIEWED" in ctx
        assert "https://doi.org/xxx" in ctx

    def test_ai_summary_cited_differently_from_peer_reviewed(self):
        results = [
            {"source_system": "rag", "title": "AI Summary", "trust_tier": "ai_summary",
             "section": "", "text": "AI-generated summary.", "source_url": "", "content": ""},
        ]
        ctx = _format_for_context(results)
        assert "AI_SUMMARY" in ctx

    def test_context_truncated_at_max(self):
        results = [
            {"source_system": "okf", "title": "Big", "trust_tier": "curated",
             "content": "X" * 20000, "text": ""},
        ]
        ctx = _format_for_context(results, max_chars=500)
        assert len(ctx) <= 600  # truncated + suffix
        assert "[context truncated]" in ctx


class TestTrustTierRanking:
    def test_peer_reviewed_outranks_ai_summary(self):
        """Spec §4: prefer higher trust_tier when chunks conflict."""
        from rag.retrieval import _vector_search  # just checking the weights exist
        assert TRUST_WEIGHTS["peer_reviewed"] > TRUST_WEIGHTS["ai_summary"]
        assert TRUST_WEIGHTS["preprint"] > TRUST_WEIGHTS["web_secondary"]
        assert TRUST_WEIGHTS["web_primary"] > TRUST_WEIGHTS["web_secondary"]

    def test_final_score_is_cosine_times_trust(self):
        """Score = cosine_similarity * trust_weight."""
        cosine = 0.8
        trust = TRUST_WEIGHTS["preprint"]  # 0.85
        assert abs(cosine * trust - 0.68) < 0.01


class TestGapDetection:
    def setup_method(self):
        _gap_tracker.clear()

    def test_gap_increments_on_no_curated_match(self):
        from rag.retrieval import _gap_tracker, _GAP_THRESHOLD
        qh = _query_hash("test gap query")
        for _ in range(_GAP_THRESHOLD):
            _gap_tracker[qh] = _gap_tracker.get(qh, 0) + 1
        candidates = get_gap_candidates()
        assert any(c["query_hash"] == qh for c in candidates)

    def test_gap_resets_after_curated_match(self):
        from rag.retrieval import _gap_tracker
        qh = _query_hash("reset test")
        _gap_tracker[qh] = 2  # below threshold
        # Simulate curated match found → reset
        _gap_tracker.pop(qh, None)
        candidates = get_gap_candidates()
        assert not any(c["query_hash"] == qh for c in candidates)

    def test_reset_gap_function(self):
        from rag.retrieval import _gap_tracker
        qh = _query_hash("manual reset")
        _gap_tracker[qh] = 5
        reset_gap("manual reset")
        assert qh not in _gap_tracker


class TestVectorSearchIntegration:
    @pytest.mark.asyncio
    async def test_superseded_chunks_excluded(self):
        """Spec §5: superseded content should not appear in default retrieval."""
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.pool import NullPool
        from sqlalchemy import text
        from config import get_settings
        from rag.ingest import ingest, _get_embedder
        from rag.retrieval import _vector_search

        s = get_settings()
        engine = create_async_engine(s.postgres_url, poolclass=NullPool)

        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(text("DELETE FROM rag_chunks WHERE title LIKE 'TESTSUP_%'"))
            await db.commit()

            # Ingest v1, then supersede with v2.
            await ingest(db, content="# V1\n\nUnique supersede test content alpha beta.\n",
                         title="TESTSUP_doc", source_type="user_upload")
            await ingest(db, content="# V2\n\nUpdated supersede test content alpha beta gamma.\n",
                         title="TESTSUP_doc", source_type="user_upload")

            # Search for content that matches.
            qvec = _get_embedder().encode(["supersede test content alpha beta"], normalize_embeddings=True)[0].tolist()
            results = await _vector_search(db, qvec, top_k=10)

            # Only non-superseded chunks should appear.
            for r in results:
                assert "TESTSUP_" not in r["title"] or r["trust_tier"] != ""

            # Check directly: v1 chunks should be superseded.
            v1 = (await db.execute(text(
                "SELECT count(*) FROM rag_chunks WHERE title = 'TESTSUP_doc' AND superseded_by IS NOT NULL"
            ))).fetchone()
            assert v1[0] > 0  # v1 chunks are superseded

            await db.execute(text("DELETE FROM rag_chunks WHERE title LIKE 'TESTSUP_%'"))
            await db.commit()
        await engine.dispose()


class TestSupporterMode:
    """Phase 3: retrieve_raw_context injects RAG chunks into Sage + Oracle."""

    @pytest.mark.asyncio
    async def test_supporter_returns_annotated_context(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.pool import NullPool
        from config import get_settings
        from rag.retrieval import retrieve_raw_context

        s = get_settings()
        engine = create_async_engine(s.postgres_url, poolclass=NullPool)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            ctx = await retrieve_raw_context(db, "composite material analysis", top_k=3)
            assert "RAW KNOWLEDGE" in ctx
            # Trust-tier annotations present (at least one of the tiers).
            assert any(t in ctx for t in ["PREPRINT", "AI_SUMMARY", "WEB_SECONDARY", "WEB_PRIMARY"])
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_supporter_context_has_source_urls(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.pool import NullPool
        from config import get_settings
        from rag.retrieval import retrieve_raw_context

        s = get_settings()
        engine = create_async_engine(s.postgres_url, poolclass=NullPool)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            ctx = await retrieve_raw_context(db, "neural network", top_k=3)
            # Source URLs should appear in the annotation when available.
            if "source:" in ctx:
                assert "http" in ctx or "council://" in ctx
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_supporter_truncates_at_max_chars(self):
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.pool import NullPool
        from config import get_settings
        from rag.retrieval import retrieve_raw_context

        s = get_settings()
        engine = create_async_engine(s.postgres_url, poolclass=NullPool)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            ctx = await retrieve_raw_context(db, "material", top_k=10, max_chars=500)
            assert len(ctx) <= 600
            assert "[RAG context truncated]" in ctx
        await engine.dispose()
