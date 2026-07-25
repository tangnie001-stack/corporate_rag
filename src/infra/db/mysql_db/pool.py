"""MySQL 连接池管理。"""

import asyncio
import aiomysql
from loguru import logger
from src.config import (
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_DATABASE,
)
from src.config.queries import (
    CREATE_TABLE_USERS,
    CREATE_TABLE_KNOWLEDGE_BASE,
    CREATE_TABLE_DOCUMENT,
    CREATE_TABLE_CONVERSATION_HISTORY,
    CREATE_TABLE_SESSIONS,
    DROP_CONVERSATION_HISTORY_FK,
)


class MySQLDB:
    """MySQL 连接池封装 — 管理连接池生命周期和表初始化。"""

    def __init__(self):
        self._pool: aiomysql.Pool | None = None
        self._pool_lock = asyncio.Lock()

    async def _get_pool(self) -> aiomysql.Pool:
        """获取或创建连接池（双重检查锁定，线程安全）。

        Returns:
            已初始化的 aiomysql 连接池实例
        """
        if self._pool is not None:
            return self._pool
        async with self._pool_lock:
            if self._pool is not None:
                return self._pool
            self._pool = await aiomysql.create_pool(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                db=MYSQL_DATABASE,
                charset="utf8mb4",
                cursorclass=aiomysql.DictCursor,
                autocommit=True,
                minsize=2,
                maxsize=10,
                connect_timeout=10,
                pool_recycle=3600,
            )
            logger.info("MySQL connection pool created (minsize=2, maxsize=10)")
            return self._pool

    async def close(self) -> None:
        """关闭连接池并释放所有资源。"""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            logger.info("MySQL connection pool closed")

    async def init_db(self) -> None:
        """初始化数据库表结构（建表 + 删除冗余外键）。"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(CREATE_TABLE_USERS)
                await cursor.execute(CREATE_TABLE_KNOWLEDGE_BASE)
                await cursor.execute(CREATE_TABLE_DOCUMENT)
                await cursor.execute(CREATE_TABLE_CONVERSATION_HISTORY)
                await cursor.execute(CREATE_TABLE_SESSIONS)
                try:
                    await cursor.execute(DROP_CONVERSATION_HISTORY_FK)
                except Exception:
                    pass
            await conn.commit()
        logger.info("Database tables initialized")
