"""add trace_id to feedback

Revision ID: 5a8c2d1e9f34
Revises: 4f7b9c1d2e30
Create Date: 2026-08-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a8c2d1e9f34"
down_revision: str | Sequence[str] | None = "4f7b9c1d2e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 反馈可经 trace_id 还原答案生成链路；存量行以空串填充（与 ORM default 一致）
    op.add_column(
        "feedback",
        sa.Column(
            "trace_id",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("feedback", "trace_id")
