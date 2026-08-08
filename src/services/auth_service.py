"""认证服务层 — 封装用户注册、登录、令牌验证等业务逻辑。"""

import uuid

from loguru import logger

from src.infra.db.mysql_db import UserRepo
from src.utils.auth_crypto import hash_password, verify_password
from src.utils.errors import BusinessError


class AuthService:
    """用户认证服务，负责注册、登录、令牌验证和登出。"""

    PASSWORD_MIN_LENGTH = 6

    def __init__(self, user_repo: UserRepo, redis_client=None):
        """初始化 AuthService。

        Args:
            user_repo: UserRepo 实例
            redis_client: Redis 客户端（可选，用于 token 缓存）
        """
        self._user_repo = user_repo
        self._redis = redis_client

    async def register(self, account: str, password: str) -> dict:
        """注册新用户。"""
        if not password or len(password) < self.PASSWORD_MIN_LENGTH:
            raise BusinessError(
                "PASSWORD_TOO_SHORT",
                f"密码长度不能少于 {self.PASSWORD_MIN_LENGTH} 位",
            )

        existing = await self._user_repo.get_user_by_account(account)
        if existing:
            raise BusinessError("ACCOUNT_EXISTS", "账号已存在")

        user_id = str(uuid.uuid4())
        password_hash = hash_password(password)
        await self._user_repo.add_user(user_id, account, password_hash)
        logger.info("User registered: user_id={} account={}", user_id, account)
        return {"user_id": user_id, "account": account}

    async def login(self, account: str, password: str) -> dict:
        """用户登录。"""
        user = await self._user_repo.get_user_by_account(account)
        if not user:
            raise BusinessError("ACCOUNT_NOT_FOUND", "账号不存在")

        if not verify_password(password, user.password):
            raise BusinessError("WRONG_PASSWORD", "密码错误")

        token = str(uuid.uuid4()).replace("-", "") + str(uuid.uuid4()).replace("-", "")
        user_id = user.id

        if self._redis:
            await self._redis.setex(f"token:{token}", 604800, user_id)
        await self._user_repo.update_user_token(user_id, token)

        logger.info("User logged in: user_id={} account={}", user_id, user.account)
        return {"token": token, "user_id": user_id}

    async def verify_token(self, token: str) -> str | None:
        """验证会话令牌有效性。"""
        if not token:
            return None

        user_id = None
        if self._redis:
            try:
                cached = await self._redis.get(f"token:{token}")
                if cached:
                    if isinstance(cached, bytes):
                        user_id = cached.decode("utf-8")
                    else:
                        user_id = cached
                    return user_id
            except Exception:  # noqa: BLE001, S110
                pass

        user = await self._user_repo.get_user_by_token(token)
        if user:
            user_id = user.id
            if self._redis and user_id:
                try:
                    await self._redis.setex(f"token:{token}", 604800, user_id)
                except Exception:  # noqa: BLE001, S110
                    pass
            return user_id

        return None

    async def logout(self, token: str) -> None:
        """退出登录，清除 Redis 中的 token 缓存。"""
        if self._redis and token:
            try:
                await self._redis.delete(f"token:{token}")
            except Exception:  # noqa: BLE001, S110
                pass
