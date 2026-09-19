"""SQLAlchemy 异步引擎与 Session 工厂。"""

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from src.config import build_postgres_dsn

DSN = build_postgres_dsn()

engine = create_async_engine(
    DSN,
    # 连接预算是共享的：同一 PostgreSQL 实例还承载 Langfuse。
    # prod 实为 --workers 4，4 × (10 + 10) ≈ 80 连接，须作为 RDS 规格的输入。
    pool_size=10,
    max_overflow=10,
    pool_recycle=3600,
    echo=False,
)

session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
)
