"""引擎方言与连接池配置的守卫测试。"""

from src.infra.db.engine import engine, run_and_dispose


def test_engine_uses_postgresql_dialect():
    """驱动必须是 asyncpg，不能还是 aiomysql。"""
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "asyncpg"


def test_pool_budget_is_declared():
    """连接预算是共享的（同一实例还有 Langfuse），必须显式且可核算。"""
    pool = engine.pool
    # engine.pool 的静态类型是基类 Pool；size/_max_overflow 是 QueuePool 的实现接口。
    assert pool.size() == 10  # pyright: ignore[reportAttributeAccessIssue]
    assert pool._max_overflow == 10  # pyright: ignore[reportAttributeAccessIssue]


def test_two_consecutive_asyncio_runs_both_succeed(monkeypatch):
    """连续两次 asyncio.run 都能成功 —— 若不释放池，第二次会抛 different loop。

    用本用例独占的引擎，避免 pytest-asyncio 会话级事件循环残留在模块级池里的连接
    干扰：那些连接绑定在会话循环上，直接复用会让第一次 run 就撞 different loop。
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.config import build_postgres_dsn
    from src.infra.db import engine as engine_module

    isolated_engine = create_async_engine(
        build_postgres_dsn(), pool_size=2, max_overflow=0
    )
    isolated_factory = async_sessionmaker(isolated_engine, expire_on_commit=False)

    async def _touch() -> int:
        async with isolated_factory() as s:
            return int(await s.scalar(text("SELECT 1")))

    monkeypatch.setattr(engine_module, "engine", isolated_engine)

    assert asyncio.run(run_and_dispose(_touch())) == 1
    assert asyncio.run(run_and_dispose(_touch())) == 1
