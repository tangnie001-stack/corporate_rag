"""事务边界原语 —— 跨表原子提交的唯一入口。

每个 Repo 方法默认自开会话并提交（单表操作的合理默认）；当一次业务动作需要
**跨表原子**时（写分块 + 更新文档状态、删分块 + 软删文档/知识库），调用方用
`session_scope(session_factory)` 打开唯一的事务边界，再把该会话传给参与的
Repo / 存储方法 —— 参与者只执行语句、**不提交**，提交与回滚由边界那一层决定。

不传 `session` 的调用点行为与改造前逐字一致：自开会话、出块提交、异常随会话关闭回滚。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def session_scope(
    session_factory, session: AsyncSession | None = None
) -> AsyncIterator[AsyncSession]:
    """提供一个会话：外部传入则复用它且不提交，否则自开、出块提交。

    Args:
        session_factory: `async_sessionmaker` 实例（`src/infra/db/engine.py`）
        session: 外部事务边界提供的会话；None 表示本层自开自提交

    Yields:
        可用于执行的 AsyncSession

    Note:
        外部传入会话时**本函数绝不提交**：提交/回滚由持有该会话的外层决定。
        自开会话路径上抛异常时不提交，`async with` 退出会关闭会话并回滚未提交的改动。
    """
    if session is not None:
        yield session
        return
    async with session_factory() as own:
        yield own
        await own.commit()
