"""RAG layer tests — validates chunker + ingestion against spec §2.3-§2.4."""
import asyncio
import pytest
from pathlib import Path

from rag.chunker import chunk_document, _estimate_tokens, _split_sections


# Writes to Postgres and loads the embedder.
pytestmark = pytest.mark.live


# --- §2.4 Structure-aware chunking ------------------------------------------

class TestChunker:
    def test_markdown_sections_become_chunk_boundaries(self):
        text = (
            "# Introduction\n\nNeural networks are models.\n\n"
            "# Methods\n\nWe used deep learning with ReLU activation.\n\n"
            "# Results\n\nAccuracy was 95%.\n"
        )
        chunks = chunk_document(text)
        sections = [c.section for c in chunks]
        assert "Introduction" in sections
        assert "Methods" in sections
        assert "Results" in sections

    def test_never_chunks_across_section_boundary(self):
        """Spec §2.4: never chunk across a section boundary."""
        text = (
            "# Methods\n\n" + "Method paragraph. " * 200 + "\n\n"
            "# Results\n\n" + "Result paragraph. " * 200 + "\n"
        )
        chunks = chunk_document(text)
        methods_chunks = [c for c in chunks if c.section == "Methods"]
        results_chunks = [c for c in chunks if c.section == "Results"]
        assert len(methods_chunks) > 1  # section was large → split
        assert len(results_chunks) > 1
        # No chunk should contain text from both sections.
        for c in methods_chunks:
            assert "Result paragraph" not in c.text
        for c in results_chunks:
            assert "Method paragraph" not in c.text

    def test_chunks_are_roughly_300_500_tokens(self):
        """Spec §2.4: small chunks (~300-500 tokens) for retrieval precision."""
        text = "# Section\n\n" + "This is a sentence about science. " * 500
        chunks = chunk_document(text)
        for c in chunks:
            # Allow some slack — sentence-boundary splitting may go slightly over.
            assert c.token_estimate <= 700 or len(chunks) == 1

    def test_parent_section_text_is_present(self):
        """Spec §2.4: parent-child relationship — chunk links to parent section."""
        text = "# Methods\n\nWe used deep learning.\n\n# Results\n\n95% accuracy.\n"
        chunks = chunk_document(text)
        for c in chunks:
            assert c.parent_section_text  # always non-empty
            assert len(c.parent_section_text) > 0

    def test_plain_text_without_headings(self):
        text = "This is a long plain text document. " * 300
        chunks = chunk_document(text)
        assert len(chunks) > 1
        for c in chunks:
            assert c.section == "(document)"

    def test_empty_text(self):
        assert chunk_document("") == []
        assert chunk_document("   ") == []

    def test_preamble_before_first_heading(self):
        text = "Some intro text before headings.\n\n# Section\n\nBody.\n"
        chunks = chunk_document(text)
        sections = [c.section for c in chunks]
        assert "(preamble)" in sections
        assert "Section" in sections


# --- §2.3 Dedupe / versioning (integration with DB) -------------------------

class TestIngestionDedupe:
    @staticmethod
    def _make_engine():
        """Create an isolated engine with NullPool (no connection sharing)."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool
        from config import get_settings
        s = get_settings()
        return create_async_engine(s.postgres_url, poolclass=NullPool)

    @pytest.mark.asyncio
    async def test_ingest_creates_chunks_with_embeddings(self):
        from sqlalchemy.ext.asyncio import AsyncSession
        from rag.ingest import ingest
        from sqlalchemy import text as sql_text

        engine = self._make_engine()
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(sql_text("DELETE FROM rag_chunks WHERE title LIKE 'TEST_%'"))
            await db.commit()

            result = await ingest(db,
                content="# Test Doc\n\nThis is test content for the RAG pipeline.\n\n# Section Two\n\nMore content here.",
                title="TEST_doc1",
                source_type="user_upload",
            )
            assert result["status"] == "ingested"
            assert result["chunks"] >= 2

            row = (await db.execute(sql_text("SELECT count(*) FROM rag_chunks WHERE title = 'TEST_doc1'"))).fetchone()
            assert row[0] >= 2
            row = (await db.execute(sql_text("SELECT count(embedding) FROM rag_chunks WHERE title = 'TEST_doc1'"))).fetchone()
            assert row[0] >= 2
            row = (await db.execute(sql_text("SELECT vector_dims(embedding) FROM rag_chunks WHERE title = 'TEST_doc1' LIMIT 1"))).fetchone()
            assert row[0] == 384

            await db.execute(sql_text("DELETE FROM rag_chunks WHERE title LIKE 'TEST_%'"))
            await db.commit()
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_exact_duplicate_is_skipped(self):
        from sqlalchemy.ext.asyncio import AsyncSession
        from rag.ingest import ingest
        from sqlalchemy import text as sql_text

        engine = self._make_engine()
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(sql_text("DELETE FROM rag_chunks WHERE title LIKE 'TEST_%'"))
            await db.commit()

            content = "# Test\n\nExact same content for dedup check.\n"
            r1 = await ingest(db, content=content, title="TEST_dedup", source_type="user_upload")
            assert r1["status"] == "ingested"
            r2 = await ingest(db, content=content, title="TEST_dedup", source_type="user_upload")
            assert r2["status"] == "skipped"
            assert "exact duplicate" in r2["reason"]

            await db.execute(sql_text("DELETE FROM rag_chunks WHERE title LIKE 'TEST_%'"))
            await db.commit()
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_near_duplicate_supersedes(self):
        from sqlalchemy.ext.asyncio import AsyncSession
        from rag.ingest import ingest
        from sqlalchemy import text as sql_text

        engine = self._make_engine()
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(sql_text("DELETE FROM rag_chunks WHERE title LIKE 'TEST_%'"))
            await db.commit()

            r1 = await ingest(db, content="# V1\n\nOriginal version content.\n", title="TEST_supersede", source_type="user_upload")
            assert r1["status"] == "ingested"
            r2 = await ingest(db, content="# V2\n\nUpdated version with different content.\n", title="TEST_supersede", source_type="user_upload")
            assert r2["status"] == "superseded"

            old = (await db.execute(sql_text(
                "SELECT superseded_by FROM rag_chunks WHERE title = 'TEST_supersede' AND content_hash = :h"
            ), {"h": r1["content_hash"]})).fetchall()
            assert all(row[0] is not None for row in old)

            await db.execute(sql_text("DELETE FROM rag_chunks WHERE title LIKE 'TEST_%'"))
            await db.commit()
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_trust_tier_defaults_by_source_type(self):
        from sqlalchemy.ext.asyncio import AsyncSession
        from rag.ingest import DEFAULT_TRUST_BY_SOURCE, ingest
        from sqlalchemy import text as sql_text

        for source_type, expected_tier in DEFAULT_TRUST_BY_SOURCE.items():
            engine = self._make_engine()
            async with AsyncSession(engine, expire_on_commit=False) as db:
                await ingest(db, content=f"Unique content for {source_type} test {expected_tier}.", title=f"TEST_tt_{source_type}", source_type=source_type)
                row = (await db.execute(sql_text(
                    f"SELECT trust_tier FROM rag_chunks WHERE title = 'TEST_tt_{source_type}' LIMIT 1"
                ))).fetchone()
                assert row[0] == expected_tier
                await db.execute(sql_text(f"DELETE FROM rag_chunks WHERE title = 'TEST_tt_{source_type}'"))
                await db.commit()
            await engine.dispose()
