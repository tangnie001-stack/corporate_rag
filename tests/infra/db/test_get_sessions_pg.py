"""`ChatRepo.get_sessions` 在 PostgreSQL 上的分组回归测试。

MySQL 允许"按主键分组"的函数依赖推断，PostgreSQL 不允许：
分组后 SELECT 的非聚合列必须全部出现在 GROUP BY 中。
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from src.infra.db.engine import session_factory
from src.infra.db.models.chat import MessageModel, SessionModel
from src.infra.db.repos import ChatRepo, KbRepo

pytestmark = pytest.mark.asyncio

_USER_ID = "get-sessions-pg-user"


@pytest_asyncio.fixture
async def seeded_session():
    """造 1 个 KB + 1 个会话 + 3 条消息，返回 (chat_repo, session_id, kb_name)。"""
    kb_repo = KbRepo(session_factory)
    chat_repo = ChatRepo(session_factory)
    suffix = uuid.uuid4().hex[:8]
    kb_name = f"get-sessions-kb-{suffix}"
    session_id = f"get-sessions-{suffix}"

    kb_id, _ = await kb_repo.get_or_create_kb(_USER_ID, kb_name)
    await chat_repo.create_session(
        SessionModel(
            id=session_id,
            user_id=_USER_ID,
            title="分组回归",
            kb_id=kb_id,
            agent="",
        )
    )
    for i in range(3):
        await chat_repo.save_message(
            MessageModel(
                session_id=session_id,
                kb_id=kb_id,
                role="user",
                content=f"m{i}",
            )
        )

    yield chat_repo, session_id, kb_name

    async with session_factory() as session:
        await session.execute(
            delete(MessageModel).where(MessageModel.session_id == session_id)
        )
        await session.execute(delete(SessionModel).where(SessionModel.id == session_id))
        await session.execute(
            text("DELETE FROM knowledge_base WHERE user_id = :u"), {"u": _USER_ID}
        )
        await session.commit()


async def test_get_sessions_returns_kb_name_and_message_count(seeded_session):
    """会话列表必须带出 join 到的 KB 名与消息数，且在 PG 上不抛 GroupingError。"""
    chat_repo, session_id, kb_name = seeded_session

    rows = await chat_repo.get_sessions(_USER_ID)

    target = [row for row in rows if row.id == session_id]
    assert len(target) == 1
    assert target[0].kb_name == kb_name
    assert target[0].message_count == 3


async def test_get_sessions_rows_expose_named_attributes(seeded_session):
    """get_sessions 的返回值必须可按属性访问（不是 raw dict）。"""
    chat_repo, session_id, _kb_name = seeded_session

    rows = await chat_repo.get_sessions(_USER_ID)

    target = [row for row in rows if row.id == session_id]
    assert len(target) == 1
    row = target[0]
    for attr in (
        "id",
        "title",
        "kb_id",
        "created_at",
        "updated_at",
        "kb_name",
        "message_count",
    ):
        assert hasattr(row, attr), f"Row 缺属性 {attr}"
