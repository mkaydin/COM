from sqlalchemy import Column, String, Integer, Float, Boolean, Text, ForeignKey, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
import uuid


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    sessions = relationship("Session", back_populates="user")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    title = Column(String(512), nullable=True)
    topic = Column(Text, nullable=True)
    status = Column(String(32), default="active", nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="sessions")
    deliberations = relationship("Deliberation", back_populates="session")


class Deliberation(Base):
    __tablename__ = "deliberations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False)
    round = Column(Integer, default=1, nullable=False)
    user_query = Column(Text, nullable=False)
    query_type = Column(String(32), nullable=True)
    status = Column(String(32), default="pending", nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session = relationship("Session", back_populates="deliberations")
    agent_responses = relationship("AgentResponse", back_populates="deliberation")
    critiques = relationship("Critique", back_populates="deliberation")
    verdict = relationship("Verdict", back_populates="deliberation", uselist=False)


class AgentResponse(Base):
    __tablename__ = "agent_responses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deliberation_id = Column(UUID(as_uuid=True), ForeignKey("deliberations.id"), nullable=False)
    agent_name = Column(String(64), nullable=False)
    response_json = Column(JSONB, nullable=False)
    confidence = Column(Float, nullable=True)
    reasoning_type = Column(String(32), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    token_count = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    deliberation = relationship("Deliberation", back_populates="agent_responses")


class Critique(Base):
    __tablename__ = "critiques"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deliberation_id = Column(UUID(as_uuid=True), ForeignKey("deliberations.id"), nullable=False)
    critique_round = Column(Integer, nullable=False)
    source_agent = Column(String(64), nullable=False)
    target_agent = Column(String(64), nullable=False)
    critique_text = Column(Text, nullable=False)
    resolved = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    deliberation = relationship("Deliberation", back_populates="critiques")


class Verdict(Base):
    __tablename__ = "verdicts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deliberation_id = Column(UUID(as_uuid=True), ForeignKey("deliberations.id"), unique=True, nullable=False)
    chosen_response_id = Column(UUID(as_uuid=True), ForeignKey("agent_responses.id"), nullable=True)
    chosen_agent = Column(String(64), nullable=True)
    user_override = Column(Text, nullable=True)
    verdict_type = Column(String(32), nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    deliberation = relationship("Deliberation", back_populates="verdict")
    chosen_response = relationship("AgentResponse", foreign_keys=[chosen_response_id])


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(512), nullable=False)
    content = Column(Text, nullable=False)
    source_type = Column(String(64), nullable=True)
    file_path = Column(Text, nullable=True)
    metadata_json = Column(JSONB, nullable=True)
    embedding = Column(Vector(1536), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_documents_embedding", "embedding", postgresql_using="ivfflat", postgresql_ops={"embedding": "vector_cosine_ops"}),
    )


class RagChunk(Base):
    """RAG chunk store — the raw/growing knowledge layer.

    Stores structure-aware chunks with proper 384-dim embeddings (all-MiniLM-L6-v2)
    and the full metadata schema from the RAG architecture spec (§3):
    source_type, trust_tier, content_hash, supersedes, promoted_to_okf, etc.
    """
    __tablename__ = "rag_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Content
    chunk_text = Column(Text, nullable=False)
    embedding = Column(Vector(384), nullable=True)

    # Structure-aware chunking metadata
    section = Column(String(512), nullable=True)
    chunk_index = Column(Integer, nullable=False, default=0)
    parent_chunk_id = Column(UUID(as_uuid=True), nullable=True)
    parent_section_text = Column(Text, nullable=True)

    # Source metadata (RAG spec §3)
    source_type = Column(String(64), nullable=False, default="user_upload")
    source_url = Column(Text, nullable=True)
    title = Column(String(512), nullable=False, default="Untitled")
    authors = Column(JSONB, nullable=True)
    publish_date = Column(DateTime(timezone=True), nullable=True)

    # Dedupe / versioning
    content_hash = Column(String(64), nullable=False, index=True)
    supersedes = Column(UUID(as_uuid=True), nullable=True)
    superseded_by = Column(UUID(as_uuid=True), nullable=True)

    # Trust + promotion
    trust_tier = Column(String(32), nullable=False, default="web_secondary")
    tags = Column(JSONB, nullable=True)
    promoted_to_okf = Column(Boolean, nullable=False, default=False)
    okf_concept_ref = Column(String(512), nullable=True)

    # Original doc link (for provenance back to Runic Library)
    source_doc_id = Column(UUID(as_uuid=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_rag_chunks_embedding", "embedding", postgresql_using="ivfflat", postgresql_ops={"embedding": "vector_cosine_ops"}),
        Index("idx_rag_chunks_hash", "content_hash"),
        Index("idx_rag_chunks_trust", "trust_tier"),
    )


class PromotionCandidate(Base):
    """Promotion queue — the seam where raw RAG chunks graduate to curated OKF
    concepts after review (spec §2.6, §4.5, §5).

    A candidate is created when:
      - Gap detection fires (same query hits vector fallback 3+ times, spec §4.5).
      - A chunk is summarized during deliberation.
      - Manually queued by the user.

    Status flow: pending → approved (wrote OKF concept) | rejected (discarded).
    """
    __tablename__ = "rag_promotion_candidates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # The chunk being considered for promotion
    chunk_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    # Why it was queued
    reason = Column(String(64), nullable=False, default="manual")
    # Options: "gap_detection", "summarized", "manual"
    trigger_query = Column(Text, nullable=True)

    # Pre-generated summary (the candidate OKF concept body)
    summary = Column(Text, nullable=True)

    # Review state
    status = Column(String(32), nullable=False, default="pending")
    # Options: "pending", "approved", "rejected"
    okf_concept_ref = Column(String(512), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_promotion_status", "status"),
    )
