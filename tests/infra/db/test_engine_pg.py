"""引擎方言与连接池配置的守卫测试。"""

from src.infra.db.engine import engine, session_factory  # noqa: F401


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
