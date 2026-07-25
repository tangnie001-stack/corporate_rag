"""知识库 Repo — knowledge_base 表 CRUD。"""

import uuid
from typing import Optional
import aiomysql
from src.config.queries import (
    INSERT_KNOWLEDGE_BASE, SELECT_KNOWLEDGE_BASE_ID_BY_NAME, SELECT_KB_NAME_BY_ID,
    SELECT_ALL_KNOWLEDGE_BASES, DELETE_KNOWLEDGE_BASE_BY_ID, SOFT_DELETE_KNOWLEDGE_BASE_BY_ID,
)
from src.infra.db.entities import KbListItem


class KbRepo:
    def __init__(self, mysql_db):
        self._pool_getter = mysql_db._get_pool

    async def get_or_create_kb(self, user_id: str, name: str, description: str = "") -> tuple[str, bool]:
        pool = await self._pool_getter()
        kb_id = str(uuid.uuid4())
        async with pool.acquire() as conn:
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(INSERT_KNOWLEDGE_BASE, (kb_id, user_id, name, description))
                await conn.commit()
                return kb_id, True
            except aiomysql.IntegrityError:
                await conn.rollback()
                existing_id = await self.get_kb_by_name(user_id, name)
                if existing_id is None:
                    raise RuntimeError(f"IntegrityError on '{name}' but get_kb_by_name returned None")
                return existing_id, False

    async def get_kb_by_name(self, user_id: str, name: str) -> Optional[str]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_KNOWLEDGE_BASE_ID_BY_NAME, (user_id, name))
                row = await cursor.fetchone()
        return row["id"] if row else None

    async def get_kb_name_by_id(self, kb_id: str) -> Optional[str]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_KB_NAME_BY_ID, (kb_id,))
                row = await cursor.fetchone()
        return row["name"] if row else None

    async def get_all_kb(self, user_id: str = "") -> list[KbListItem]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_ALL_KNOWLEDGE_BASES, (user_id,))
                result = [
                    KbListItem(
                        id=row["id"],
                        user_id=row["user_id"],
                        name=row["name"],
                        doc_count=row["doc_count"],
                    )
                    for row in await cursor.fetchall()
                ]
            await conn.commit()
        return result

    async def delete_kb(self, kb_id: str) -> bool:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(DELETE_KNOWLEDGE_BASE_BY_ID, (kb_id,))
                deleted = cursor.rowcount
            await conn.commit()
        return deleted > 0

    async def soft_delete_kb(self, kb_id: str) -> bool:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SOFT_DELETE_KNOWLEDGE_BASE_BY_ID, (kb_id,))
                deleted = cursor.rowcount
            await conn.commit()
        return deleted > 0
