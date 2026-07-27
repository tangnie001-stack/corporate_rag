"""知识库 Repo — knowledge_base 表 CRUD。"""

import uuid
from typing import Optional
import aiomysql
from src.config.queries import (
    INSERT_KNOWLEDGE_BASE,
    SELECT_KNOWLEDGE_BASE_ID_BY_NAME,
    SELECT_KB_NAME_BY_ID,
    SELECT_ALL_KNOWLEDGE_BASES,
    DELETE_KNOWLEDGE_BASE_BY_ID,
    SOFT_DELETE_KNOWLEDGE_BASE_BY_ID,
)
from src.infra.db.entities import KbListItem


class KbRepo:
    """知识库 CRUD 仓库。

    封装 knowledge_bases 表的所有查询操作，返回 KbEntity / KbListItem 等类型对象。
    """

    def __init__(self, mysql_db):
        """初始化 KbRepo。

        Args:
            mysql_db: MySQLDB 实例，用于获取连接池
        """
        self._pool_getter = mysql_db._get_pool

    async def get_or_create_kb(
        self, user_id: str, name: str, description: str = ""
    ) -> tuple[str, bool]:
        """按用户和名称查找或创建知识库。

        先尝试插入，若唯一键冲突则查找已有记录。

        Args:
            user_id: 用户 UUID
            name: 知识库名称
            description: 知识库描述（可选）

        Returns:
            (知识库 ID, 是否新创建) 的元组

        Raises:
            RuntimeError: 唯一键冲突但通过名称查找返回 None 时
        """
        pool = await self._pool_getter()
        kb_id = str(uuid.uuid4())
        async with pool.acquire() as conn:
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        INSERT_KNOWLEDGE_BASE, (kb_id, user_id, name, description)
                    )
                await conn.commit()
                return kb_id, True
            except aiomysql.IntegrityError:
                await conn.rollback()
                existing_id = await self.get_kb_by_name(user_id, name)
                if existing_id is None:
                    raise RuntimeError(
                        f"IntegrityError on '{name}' but get_kb_by_name returned None"
                    )
                return existing_id, False

    async def get_kb_by_name(self, user_id: str, name: str) -> Optional[str]:
        """按用户和名称查询知识库 ID。

        Args:
            user_id: 用户 UUID
            name: 知识库名称

        Returns:
            知识库 ID，不存在时返回 None
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_KNOWLEDGE_BASE_ID_BY_NAME, (user_id, name))
                row = await cursor.fetchone()
        return row["id"] if row else None

    async def get_kb_name_by_id(self, kb_id: str) -> Optional[str]:
        """按知识库 ID 查询知识库名称。

        Args:
            kb_id: 知识库 UUID

        Returns:
            知识库名称，不存在时返回 None
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_KB_NAME_BY_ID, (kb_id,))
                row = await cursor.fetchone()
        return row["name"] if row else None

    async def get_all_kb(self, user_id: str = "") -> list[KbListItem]:
        """获取用户的所有知识库列表。

        Args:
            user_id: 用户 UUID，为空时返回所有用户的知识库

        Returns:
            知识库列表（含文档数量）
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_ALL_KNOWLEDGE_BASES, (user_id,))
                result = [
                    KbListItem(
                        id=row["id"],
                        user_id=row["user_id"],
                        name=row["name"],
                        description=row.get("description"),
                        doc_count=row["doc_count"],
                    )
                    for row in await cursor.fetchall()
                ]
            await conn.commit()
        return result

    async def delete_kb(self, kb_id: str) -> bool:
        """物理删除知识库。

        Args:
            kb_id: 知识库 UUID

        Returns:
            是否删除了记录
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(DELETE_KNOWLEDGE_BASE_BY_ID, (kb_id,))
                deleted = cursor.rowcount
            await conn.commit()
        return deleted > 0

    async def soft_delete_kb(self, kb_id: str) -> bool:
        """软删除知识库（标记为已删除）。

        Args:
            kb_id: 知识库 UUID

        Returns:
            是否更新了记录
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SOFT_DELETE_KNOWLEDGE_BASE_BY_ID, (kb_id,))
                deleted = cursor.rowcount
            await conn.commit()
        return deleted > 0
