"""事务边界原语的语义：外部会话不提交、自开会话提交、异常回滚（真实 PG）。"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.infra.db.engine import session_factory
from src.infra.db.transaction import session_scope

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def kb_row():
    """建一行真实知识库（用于断言提交/回滚后的可见性），测后清理。"""
    kb_id = f"p4tx-{uuid.uuid4().hex[:12]}"
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO knowledge_base (id, user_id, name, description, doc_count, is_deleted)"
                " VALUES (:k, 'p4test', :n, '', 0, 0)"
            ),
            {"k": kb_id, "n": f"p4-{kb_id[-6:]}"},
        )
        await s.commit()
    yield kb_id
    async with session_factory() as s:
        await s.execute(text("DELETE FROM knowledge_base WHERE id = :k"), {"k": kb_id})
        await s.commit()


async def _count(kb_id: str) -> int:
    """另开一个会话读该行的可见性（用独立会话，避免读到本会话未提交的状态）。"""
    async with session_factory() as s:
        return int(
            await s.scalar(
                text("SELECT count(*) FROM knowledge_base WHERE id = :k"), {"k": kb_id}
            )
        )


async def test_owning_scope_commits(kb_row):
    """不传 session 时，出块即提交。"""
    marker = f"own-{uuid.uuid4().hex[:8]}"
    async with session_scope(session_factory) as s:
        await s.execute(
            text("UPDATE knowledge_base SET description = :d WHERE id = :k"),
            {"d": marker, "k": kb_row},
        )
    async with session_factory() as s:
        value = await s.scalar(
            text("SELECT description FROM knowledge_base WHERE id = :k"), {"k": kb_row}
        )
    assert value == marker


async def test_participating_scope_does_not_commit(kb_row):
    """传了 session 时不得提交：外层不提交，改动对别的会话不可见。"""
    marker = f"part-{uuid.uuid4().hex[:8]}"
    async with session_factory() as outer:
        async with session_scope(session_factory, outer) as s:
            assert s is outer
            await s.execute(
                text("UPDATE knowledge_base SET description = :d WHERE id = :k"),
                {"d": marker, "k": kb_row},
            )
        # outer 尚未提交
        await outer.rollback()
    async with session_factory() as s:
        value = await s.scalar(
            text("SELECT description FROM knowledge_base WHERE id = :k"), {"k": kb_row}
        )
    assert value != marker


async def test_exception_rolls_back(kb_row):
    """自开会话路径上抛异常时不得留下任何改动。"""
    marker = f"boom-{uuid.uuid4().hex[:8]}"
    with pytest.raises(RuntimeError):
        async with session_scope(session_factory) as s:
            await s.execute(
                text("UPDATE knowledge_base SET description = :d WHERE id = :k"),
                {"d": marker, "k": kb_row},
            )
            raise RuntimeError("inject")
    async with session_factory() as s:
        value = await s.scalar(
            text("SELECT description FROM knowledge_base WHERE id = :k"), {"k": kb_row}
        )
    assert value != marker


async def test_kb_row_still_exists(kb_row):
    """夹具本身：确认那一行真的写进去了（否则上面三条断言都是空转）。"""
    assert await _count(kb_row) == 1


async def test_repo_method_with_outer_session_does_not_commit(kb_row):
    """Repo 方法在外部会话下只执行语句：外层回滚后改动不可见。"""
    from src.infra.db.repos.kb_repo import KbRepo

    repo = KbRepo(session_factory)
    async with repo.transaction() as s:
        ok = await repo.soft_delete_kb(kb_row, session=s)
        assert ok is True
        await s.rollback()
    async with session_factory() as s:
        is_deleted = await s.scalar(
            text("SELECT is_deleted FROM knowledge_base WHERE id = :k"), {"k": kb_row}
        )
    assert is_deleted == 0


async def test_repo_method_without_session_still_commits(kb_row):
    """不传 session 时行为与改造前一致：出块即提交。"""
    from src.infra.db.repos.kb_repo import KbRepo

    repo = KbRepo(session_factory)
    assert await repo.soft_delete_kb(kb_row) is True
    async with session_factory() as s:
        is_deleted = await s.scalar(
            text("SELECT is_deleted FROM knowledge_base WHERE id = :k"), {"k": kb_row}
        )
    assert is_deleted == 1
