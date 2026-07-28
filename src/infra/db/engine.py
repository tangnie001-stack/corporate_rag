"""SQLAlchemy 异步引擎与 Session 工厂。"""

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from src.config import (
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_DATABASE,
)

DSN = (
    f"mysql+aiomysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
)

engine = create_async_engine(
    DSN,
    pool_size=10,
    max_overflow=10,
    pool_recycle=3600,
    echo=False,
)

session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
)
