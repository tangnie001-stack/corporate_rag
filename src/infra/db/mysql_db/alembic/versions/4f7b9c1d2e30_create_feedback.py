"""create feedback table

Revision ID: 4f7b9c1d2e30
Revises: e6304ba3a9ef
Create Date: 2026-08-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.infra.db.base import UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "4f7b9c1d2e30"
down_revision: str | Sequence[str] | None = "e6304ba3a9ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "feedback",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("message_index", sa.Integer(), nullable=False),
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            UTCDateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("feedback")
