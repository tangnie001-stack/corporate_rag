"""幂等写入的语义守卫测试（需真实 PostgreSQL）。

这些是特性化测试：改写实现前后行为必须完全一致。
"""

from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.infra.db.engine import session_factory
from src.infra.db.mysql_db import ChatRepo, KbRepo

pytestmark = pytest.mark.asyncio


@dataclass
class _SessionStub:
    """create_session 需要的最小会话对象（对应 ChatRepo.create_session 的入参）。"""

    id: str
    user_id: str
    title: str
    kb_id: str
    agent: str


async def _scalar(sql: str, **params):
    async with session_factory() as session:
        return (await session.execute(text(sql), params)).scalar_one()


@pytest_asyncio.fixture
async def chat_repo():
    repo = ChatRepo(session_factory)
    yield repo
    async with session_factory() as session:
        await session.execute(text("DELETE FROM sessions WHERE id LIKE 'upsert-%'"))
        await session.commit()


@pytest_asyncio.fixture
async def kb_repo():
    repo = KbRepo(session_factory)
    yield repo
    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM knowledge_base WHERE user_id = 'upsert-user'")
        )
        await session.commit()


async def test_create_session_twice_is_idempotent(chat_repo):
    """同一 session_id 连续创建两次不得抛错，且只留一行。"""
    stub = _SessionStub(
        id="upsert-sess-1", user_id="u1", title="t", kb_id="kb1", agent=""
    )

    await chat_repo.create_session(stub)
    await chat_repo.create_session(stub)

    assert (
        await _scalar("SELECT count(*) FROM sessions WHERE id = 'upsert-sess-1'") == 1
    )


async def test_create_session_does_not_overwrite_existing(chat_repo):
    """已存在时必须保留原值（等价于 on_conflict_do_nothing，不是 do_update）。"""
    await chat_repo.create_session(
        _SessionStub(
            id="upsert-sess-2", user_id="u1", title="原值", kb_id="kb1", agent=""
        )
    )
    await chat_repo.create_session(
        _SessionStub(
            id="upsert-sess-2", user_id="u1", title="新值", kb_id="kb2", agent="x"
        )
    )

    assert (
        await _scalar("SELECT title FROM sessions WHERE id = 'upsert-sess-2'") == "原值"
    )
    assert (
        await _scalar("SELECT kb_id FROM sessions WHERE id = 'upsert-sess-2'") == "kb1"
    )


async def test_get_or_create_kb_is_idempotent(kb_repo):
    """同名第二次必须返回同一 id，且 created 为 False。"""
    first_id, first_created = await kb_repo.get_or_create_kb("upsert-user", "upsert-kb")
    second_id, second_created = await kb_repo.get_or_create_kb(
        "upsert-user", "upsert-kb"
    )

    assert first_id == second_id
    assert first_created is True
    assert second_created is False


async def test_get_or_create_kb_revives_soft_deleted(kb_repo):
    """软删过的同名 KB 必须被复活（同 id），并返回 created=True。"""
    kb_id, _ = await kb_repo.get_or_create_kb("upsert-user", "upsert-kb-soft")
    assert await kb_repo.soft_delete_kb(kb_id) is True

    revived_id, created = await kb_repo.get_or_create_kb(
        "upsert-user", "upsert-kb-soft"
    )

    assert revived_id == kb_id
    assert created is True
    assert (
        await _scalar("SELECT is_deleted FROM knowledge_base WHERE id = :i", i=kb_id)
        == 0
    )
