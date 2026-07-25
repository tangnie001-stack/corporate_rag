"""认证服务层 — 封装用户注册、登录、令牌验证等业务逻辑。"""

import uuid
from typing import Optional

from loguru import logger

from src.infra.db.mysql_db import MySQLDB
from src.utils.errors import BusinessError
from src.utils.auth_crypto import hash_password, verify_password


class AuthService:
    """用户认证服务，负责注册、登录、令牌验证和登出。

    AuthService 接收 MySQLDB 和可选的 Redis 客户端，不依赖 AppService。
    密码哈希和校验委托给 src.utils.auth_crypto 模块。
    """

    PASSWORD_MIN_LENGTH = 6

    def __init__(self, db: MySQLDB, redis_client=None):
        """初始化 AuthService。

        Args:
            db: MySQLDB 实例（用于用户 CRUD）
            redis_client: Redis 客户端（可选，用于 token 缓存）
        """
        self._db = db
        self._redis = redis_client

    async def register(self, account: str, password: str) -> dict:
        """注册新用户。

        Args:
            account: 登录账号
            password: 明文密码

        Returns:
            dict: 包含 user_id 和 account 的字典

        Raises:
            BusinessError: 账号已存在或密码不符合要求
        """
        if not password or len(password) < self.PASSWORD_MIN_LENGTH:
            raise BusinessError(
                "PASSWORD_TOO_SHORT",
                "密码长度不能少于 {} 位".format(self.PASSWORD_MIN_LENGTH),
            )

        existing = await self._db.get_user_by_account(account)
        if existing:
            raise BusinessError("ACCOUNT_EXISTS", "账号已存在")

        user_id = str(uuid.uuid4())
        password_hash = hash_password(password)
        await self._db.add_user(user_id, account, password_hash)
        logger.info("User registered: user_id={} account={}", user_id, account)
        return {"user_id": user_id, "account": account}

    async def login(self, account: str, password: str) -> dict:
        """用户登录。

        Args:
            account: 登录账号
            password: 明文密码

        Returns:
            dict: 包含 token 和 user_id 的字典

        Raises:
            BusinessError: 账号不存在或密码错误
        """
        user = await self._db.get_user_by_account(account)
        if not user:
            raise BusinessError("ACCOUNT_NOT_FOUND", "账号不存在")

        if not verify_password(password, user["password"]):
            raise BusinessError("WRONG_PASSWORD", "密码错误")

        token = str(uuid.uuid4()).replace("-", "") + str(uuid.uuid4()).replace("-", "")
        user_id = user["id"]

        # 写入 Redis 缓存（TTL: 7 天）
        if self._redis:
            await self._redis.setex("token:{}".format(token), 604800, user_id)
        # 更新 MySQL token
        await self._db.update_user_token(user_id, token)

        logger.info("User logged in: user_id={} account={}", user_id, account)
        return {"token": token, "user_id": user_id}

    async def verify_token(self, token: str) -> Optional[str]:
        """验证会话令牌有效性。

        Args:
            token: 会话令牌

        Returns:
            有效的 user_id，无效时返回 None
        """
        if not token:
            return None

        # 优先查 Redis
        user_id = None
        if self._redis:
            try:
                cached = await self._redis.get("token:{}".format(token))
                if cached:
                    user_id = (
                        cached.decode("utf-8") if isinstance(cached, bytes) else cached
                    )
                    return user_id
            except Exception:
                pass

        # 回退查 MySQL
        user = await self._db.get_user_by_token(token)
        if user:
            user_id = user["id"]
            # 同步回 Redis
            if self._redis and user_id:
                try:
                    await self._redis.setex("token:{}".format(token), 604800, user_id)
                except Exception:
                    pass
            return user_id

        return None

    async def logout(self, token: str) -> None:
        """退出登录，清除 Redis 中的 token 缓存。

        Args:
            token: 要清除的会话令牌
        """
        if self._redis and token:
            try:
                await self._redis.delete("token:{}".format(token))
            except Exception:
                pass
