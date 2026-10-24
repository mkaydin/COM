"""Record which embedder produced each vector

`rag_chunks.embedding_model` holds `backend:model`. Without it, changing
`EMBEDDING_MODEL` silently mixed vector spaces: old chunks stayed in the index,
nothing errored, and retrieval quietly got worse.

Existing rows are left NULL rather than stamped with the current model. That
would be a guess: the vectors may have been produced by a model this database
no longer has configured. NULL means "unknown", the health endpoint reports it,
and `backend/tools/reembed.py` resolves it.

Revision ID: 4b7d2c9e1a35
Revises: 9f3c1a7d2e64
Create Date: 2026-10-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "4b7d2c9e1a35"
down_revision: Union[str, None] = "9f3c1a7d2e64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "rag_chunks",
        sa.Column("embedding_model", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rag_chunks", "embedding_model")
