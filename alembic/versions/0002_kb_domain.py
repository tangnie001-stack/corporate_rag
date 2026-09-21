"""add knowledge_base.domain

知识库领域标识：作为"知识库 → 领域默认 base"的唯一事实源。
server_default 必须是 'general' 且 nullable=False —— 否则 ALTER 后存量行为 NULL，
"存量 KB 落入通用领域"不成立，读取时只能永远走代码回退分支。

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-21

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为 knowledge_base 增加领域列，存量行取保留值 general。"""
    op.add_column(
        "knowledge_base",
        sa.Column(
            "domain",
            sa.String(length=32),
            nullable=False,
            server_default="general",
            comment="知识库领域（prompt base 三选一依据；取值须有对应 base 模板）",
        ),
    )


def downgrade() -> None:
    """移除领域列。"""
    op.drop_column("knowledge_base", "domain")
