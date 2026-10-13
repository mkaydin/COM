"""rag store + verdict.chosen_agent

The initial schema revision predates the RAG layer, so `rag_chunks` and
`rag_promotion_candidates` were never migrated — they only existed because
`init_db()` runs `Base.metadata.create_all`. This brings the schema in line
with `db.models`.

Revision ID: c3d91a5f7be2
Revises: aeb264f6ae36
Create Date: 2026-10-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "c3d91a5f7be2"
down_revision: Union[str, None] = "aeb264f6ae36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rag_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        # Content
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        # Structure-aware chunking metadata
        sa.Column("section", sa.String(length=512), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parent_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_section_text", sa.Text(), nullable=True),
        # Source metadata
        sa.Column("source_type", sa.String(length=64), nullable=False, server_default="user_upload"),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False, server_default="Untitled"),
        sa.Column("authors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("publish_date", sa.DateTime(timezone=True), nullable=True),
        # Dedupe / versioning
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
        # Trust + promotion
        sa.Column("trust_tier", sa.String(length=32), nullable=False, server_default="web_secondary"),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("promoted_to_okf", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("okf_concept_ref", sa.String(length=512), nullable=True),
        sa.Column("source_doc_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_rag_chunks_content_hash", "rag_chunks", ["content_hash"], unique=False)
    op.create_index("idx_rag_chunks_hash", "rag_chunks", ["content_hash"], unique=False)
    op.create_index("idx_rag_chunks_trust", "rag_chunks", ["trust_tier"], unique=False)
    op.create_index(
        "idx_rag_chunks_embedding",
        "rag_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="ivfflat",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "rag_promotion_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False, server_default="manual"),
        sa.Column("trigger_query", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("okf_concept_ref", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_rag_promotion_candidates_chunk_id", "rag_promotion_candidates", ["chunk_id"], unique=False)
    op.create_index("idx_promotion_status", "rag_promotion_candidates", ["status"], unique=False)

    # Records which agent the user picked, without clobbering query_type
    # (the router's factual/relational/... classification).
    op.add_column("verdicts", sa.Column("chosen_agent", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("verdicts", "chosen_agent")

    op.drop_index("idx_promotion_status", table_name="rag_promotion_candidates")
    op.drop_index("ix_rag_promotion_candidates_chunk_id", table_name="rag_promotion_candidates")
    op.drop_table("rag_promotion_candidates")

    op.drop_index("idx_rag_chunks_embedding", table_name="rag_chunks", postgresql_using="ivfflat")
    op.drop_index("idx_rag_chunks_trust", table_name="rag_chunks")
    op.drop_index("idx_rag_chunks_hash", table_name="rag_chunks")
    op.drop_index("ix_rag_chunks_content_hash", table_name="rag_chunks")
    op.drop_table("rag_chunks")
