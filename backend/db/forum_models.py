"""Forum domain models — topics, posts, and the API intake queue.

Deliberately separate from `db/models.py`. The tables in `db/models.py` are the
*record* of what happened (deliberations, agent responses, verdicts) and are
consumed by Archive, PDF export and OKF filing. The forum is the *authoring
surface*: a persistent thread where characters talk to each other in order,
each post carrying its own stance and reply target.

Each topic run still writes a `Deliberation` so the Archive keeps working —
the forum does not replace that record, it composes on top of it.

`Post.seq` is a per-topic monotonic counter with a UNIQUE constraint. The
`exchange` node writes posts **sequentially** rather than via `asyncio.gather`
precisely so this cannot collide; the constraint is the backstop, not the plan.
"""

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    Text,
    ForeignKey,
    DateTime,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from db.models import Base


class LlmRole(Base):
    """Runtime override binding a role (moderator, oracle, …) to a model id.

    `Settings` holds the env-configured defaults; this table holds whatever the
    user changed at runtime via `POST /api/llm/roles`, so a per-character model
    choice survives a restart. See `llm/roles.py`.
    """

    __tablename__ = "llm_roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role = Column(String(64), nullable=False, unique=True)
    model_id = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("idx_llm_roles_role", "role"),)


class Topic(Base):
    """A forum thread — one question, opened by a user, an API, or the Moderator."""

    __tablename__ = "topics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    #: Optional link back to the deliberation record / Archive session.
    session_id = Column(UUID(as_uuid=True), nullable=True)
    deliberation_id = Column(UUID(as_uuid=True), nullable=True)

    title = Column(Text, nullable=False)
    #: The raw seed question exactly as submitted.
    question = Column(Text, nullable=False)
    #: The Moderator's framing of the question (posted as the opening post).
    framing = Column(Text, nullable=True)

    #: 'user' | 'api' | 'moderator'
    origin = Column(String(32), nullable=False, default="user")
    #: 'open' | 'debating' | 'concluded' | 'archived'
    status = Column(String(32), nullable=False, default="open", index=True)
    #: 'consensus' | 'plurality' | 'diverse' — requested mode, may be 'auto'.
    mode = Column(String(32), nullable=False, default="auto")
    #: What actually happened: 'consensus' | 'plurality' | None.
    outcome = Column(String(32), nullable=True)

    #: Ordered character keys that were invited to speak.
    cast = Column(JSONB, nullable=False, default=list)
    tags = Column(JSONB, nullable=False, default=list)

    #: Provenance for API intake — request id, source system, raw payload.
    source_meta = Column(JSONB, nullable=True)

    #: The Moderator's final synthesis (consensus answer or plurality positions).
    synthesis = Column(JSONB, nullable=True)
    #: Deterministic convergence signals, kept alongside the LLM's own opinion.
    signals = Column(JSONB, nullable=True)

    max_rounds = Column(Integer, nullable=False, default=2)
    round_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    concluded_at = Column(DateTime(timezone=True), nullable=True)

    posts = relationship(
        "Post", back_populates="topic", cascade="all, delete-orphan", order_by="Post.seq"
    )

    __table_args__ = (
        Index("idx_topics_status_created", "status", "created_at"),
        Index("idx_topics_session", "session_id"),
    )


class Post(Base):
    """One message in a topic, authored by a character, the Moderator, or the user."""

    __tablename__ = "posts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    topic_id = Column(UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)

    #: Monotonic, gap-free per topic. UNIQUE — the ordering is the product.
    seq = Column(Integer, nullable=False)
    #: Which exchange round produced this (0 = opening statements).
    round = Column(Integer, nullable=False, default=0)

    #: 'user' | 'moderator' | 'character'
    author_kind = Column(String(32), nullable=False)
    #: Character key, or 'moderator' / 'user'.
    author = Column(String(64), nullable=False, index=True)

    #: Markdown body.
    body = Column(Text, nullable=False)

    #: 'open' | 'propose' | 'support' | 'challenge' | 'question' | 'concede' | 'synthesize'
    stance = Column(String(32), nullable=False, default="propose")

    #: The post this one responds to — makes the exchange legible.
    reply_to = Column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="SET NULL"), nullable=True)

    confidence = Column(Float, nullable=True)
    #: Structured claims extracted from the body (deterministic convergence uses these).
    claims = Column(JSONB, nullable=False, default=list)
    citations = Column(JSONB, nullable=False, default=list)
    #: Reasoning trace, retrieval stats, raw agent output — anything extra.
    extra = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    topic = relationship("Topic", back_populates="posts")
    replies = relationship("Post", backref="replies_to", remote_side=[reply_to])

    __table_args__ = (
        UniqueConstraint("topic_id", "seq", name="uq_posts_topic_seq"),
        Index("idx_posts_topic_seq", "topic_id", "seq"),
    )


class TopicIntake(Base):
    """Questions fed in by an external system, awaiting the Moderator.

    `POST /api/forum/intake` lands here as `queued`. The Moderator drains it
    into real Topics (`opened`), or a human rejects it. Keeps API traffic from
    being able to block on a full thread run.
    """

    __tablename__ = "topic_intake"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    #: 'queued' | 'opened' | 'rejected'
    status = Column(String(32), nullable=False, default="queued", index=True)

    question = Column(Text, nullable=False)
    #: Free-form: which system sent this, request id, priority, etc.
    source = Column(String(128), nullable=True)
    payload = Column(JSONB, nullable=True)

    #: Optional caller-supplied options (mode, cast, tags).
    options = Column(JSONB, nullable=True)

    topic_id = Column(UUID(as_uuid=True), nullable=True)
    reject_reason = Column(Text, nullable=True)

    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    opened_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("idx_intake_status", "status"),)