"""Post stage and revision link

`posts.kind` records which stage produced a post (statement / reply /
revision / synthesis / user) and `posts.revision_of` points a revision at the
post it supersedes. Before this, a character's "final answer" could only be
guessed at from `stance`, which describes how a post argues rather than when it
was produced.

Revision ID: 9f3c1a7d2e64
Revises: d4e82f1a9b73
Create Date: 2026-10-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9f3c1a7d2e64"
down_revision: Union[str, None] = "d4e82f1a9b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            server_default="reply",
        ),
    )
    op.add_column(
        "posts",
        sa.Column("revision_of", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_posts_revision_of",
        "posts",
        "posts",
        ["revision_of"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_posts_revision_of", "posts", type_="foreignkey")
    op.drop_column("posts", "revision_of")
    op.drop_column("posts", "kind")
