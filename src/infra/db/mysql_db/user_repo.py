"""用户 Repo — users 表 CRUD。"""

from typing import Optional
from src.config.queries import INSERT_USER, SELECT_USER_BY_ACCOUNT, UPDATE_USER_TOKEN, SELECT_USER_BY_TOKEN
from src.infra.db.entities import UserEntity


class UserRepo:
    def __init__(self, mysql_db):
        self._pool_getter = mysql_db._get_pool

    async def add_user(self, user_id: str, account: str, password_hash: str) -> None:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(INSERT_USER, (user_id, account, password_hash))
            await conn.commit()

    async def get_user_by_account(self, account: str) -> Optional[UserEntity]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_USER_BY_ACCOUNT, (account,))
                row = await cursor.fetchone()
        if not row:
            return None
        return UserEntity(**row)

    async def update_user_token(self, user_id: str, token: str) -> None:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(UPDATE_USER_TOKEN, (token, user_id))
            await conn.commit()

    async def get_user_by_token(self, token: str) -> Optional[UserEntity]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_USER_BY_TOKEN, (token,))
                row = await cursor.fetchone()
        if not row:
            return None
        return UserEntity(**row)
