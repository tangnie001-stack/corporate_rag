"""用户 Repo — users 表 CRUD。"""

from typing import Optional
from src.config.queries import (
    INSERT_USER,
    SELECT_USER_BY_ACCOUNT,
    UPDATE_USER_TOKEN,
    SELECT_USER_BY_TOKEN,
)
from src.infra.db.entities import UserEntity


class UserRepo:
    """用户 CRUD 仓库。

    封装 users 表的所有查询操作，返回 UserEntity 类型对象。
    """

    def __init__(self, mysql_db):
        """初始化 UserRepo。

        Args:
            mysql_db: MySQLDB 实例，用于获取连接池
        """
        self._pool_getter = mysql_db._get_pool

    async def add_user(self, user_id: str, account: str, password_hash: str) -> None:
        """插入一条用户记录。

        Args:
            user_id: 用户 UUID
            account: 用户账号
            password_hash: 密码哈希值
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(INSERT_USER, (user_id, account, password_hash))
            await conn.commit()

    async def get_user_by_account(self, account: str) -> Optional[UserEntity]:
        """按账号查询用户。

        Args:
            account: 用户账号

        Returns:
            用户实体，不存在时返回 None
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_USER_BY_ACCOUNT, (account,))
                row = await cursor.fetchone()
        if not row:
            return None
        return UserEntity(**row)

    async def update_user_token(self, user_id: str, token: str) -> None:
        """更新用户的登录令牌。

        Args:
            user_id: 用户 UUID
            token: 新的登录令牌值
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(UPDATE_USER_TOKEN, (token, user_id))
            await conn.commit()

    async def get_user_by_token(self, token: str) -> Optional[UserEntity]:
        """按登录令牌查询用户。

        Args:
            token: 登录令牌

        Returns:
            用户实体，不存在时返回 None
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_USER_BY_TOKEN, (token,))
                row = await cursor.fetchone()
        if not row:
            return None
        return UserEntity(**row)
