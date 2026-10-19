"""forum tables + llm_roles, and a per-role provider column.

The two earlier revisions predate the forum, so `topics`, `posts`,
`topic_intake` and `llm_roles` were never migrated — they existed only because
`init_db()` runs `Base.metadata.create_all`. A database built purely by
`alembic upgrade head` had no forum in it at all.

`create_all` is also what put `ON DELETE CASCADE` on `posts.topic_id`, which
the integration test asserts, so the cascade is reproduced here explicitly
rather than left implicit.

This revision also adds `llm_roles.provider`. A role used to name only a model
on whichever single provider was globally active; per-character provider
selection needs the binding to record which provider the model belongs to, and
`provider_id` may be NULL for rows pinned before this revision.

Revision ID: d4e82f1a9b73
Revises: c3d91a5f7be2
Create Date: 2026-10-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e82f1a9b73"
down_revision: Union[str, None] = "c3d91a5f7be2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return bool(op.get_bind().dialect.has_table(op.get_bind(), name))


def upgrade() -> None:
    # --- llm_roles -----------------------------------------------------------------
    # May already exist on a database that came up through `create_all`.
    if not _has_table("llm_roles"):
        op.create_table(
            "llm_roles",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("role", sa.String(length=64), nullable=False, unique=True),
            sa.Column("model_id", sa.String(length=255), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )
        op.create_index("idx_llm_roles_role", "llm_roles", ["role"], unique=False)

    # `provider` is nullable so a row pinned before this revision keeps working:
    # NULL means "whatever provider is globally active", the old behaviour.
    op.add_column(
        "llm_roles",
        sa.Column("provider", sa.String(length=64), nullable=True),
    )

    # --- topics --------------------------------------------------------------------
    if not _has_table("topics"):
        op.create_table(
            "topics",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "session_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True,
            ),
            sa.Column(
                "deliberation_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("deliberations.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column("title", sa.String(length=512), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("framing", sa.Text(), nullable=True),
            sa.Column("origin", sa.String(length=32), nullable=False, server_default="user"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
            sa.Column("mode", sa.String(length=32), nullable=False, server_default="auto"),
            sa.Column("outcome", sa.String(length=32), nullable=True),
            sa.Column("cast", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
            sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
            sa.Column("source_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("synthesis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("max_rounds", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("round_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("concluded_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_topics_status", "topics", ["status"], unique=False)
        op.create_index("idx_topics_session", "topics", ["session_id"], unique=False)
        op.create_index(
            "idx_topics_status_created", "topics", ["status", "created_at"], unique=False
        )

    # --- posts ---------------------------------------------------------------------
    if not _has_table("posts"):
        op.create_table(
            "posts",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            # The cascade the forum relies on: deleting a topic takes its thread.
            sa.Column(
                "topic_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False,
            ),
            # Per-topic monotonic counter. The graph writes posts sequentially and
            # this constraint is the backstop, not the plan.
            sa.Column("seq", sa.Integer(), nullable=False),
            sa.Column("round", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("author_kind", sa.String(length=32), nullable=False, server_default="character"),
            sa.Column("author", sa.String(length=64), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("stance", sa.String(length=32), nullable=False),
            # Self-referential reply target; deleting the *parent post* must not
            # take the replies with it.
            sa.Column(
                "reply_to", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("posts.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("claims", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
            sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
            sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.UniqueConstraint("topic_id", "seq", name="uq_posts_topic_seq"),
        )
        op.create_index("idx_posts_topic_seq", "posts", ["topic_id", "seq"], unique=False)
        op.create_index("ix_posts_author", "posts", ["author"], unique=False)

    # --- topic_intake --------------------------------------------------------------
    if not _has_table("topic_intake"):
        op.create_table(
            "topic_intake",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=128), nullable=True),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column(
                "topic_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("topics.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column("reject_reason", sa.Text(), nullable=True),
            sa.Column(
                "received_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_intake_status", "topic_intake", ["status"], unique=False)

    # --- topics.error ---------------------------------------------------------------
    # Added later than the table itself, so an existing `topics` needs it too.
    # A run that raises used to leave the topic `debating` forever with nothing
    # scheduled to change it, and nowhere to record why it stopped.
    if _has_table("topics"):
        existing = {
            c["name"] for c in sa.inspect(op.get_bind()).get_columns("topics")
        }
        if "error" not in existing:
            op.add_column("topics", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_roles", "provider")
    for table in ("topic_intake", "posts", "topics"):
        if _has_table(table):
            # `topics.error` goes with the table.
            op.drop_table(table)
    if _has_table("llm_roles"):
        op.drop_table("llm_roles")