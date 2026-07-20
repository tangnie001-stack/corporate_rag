import hashlib
import uuid
from typing import Optional

from src.config import settings


class UserAuth:
    TOKEN_TTL = settings.AUTH_TOKEN_TTL

    @staticmethod
    def hash_password(password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        return UserAuth.hash_password(password) == password_hash

    @staticmethod
    def generate_token() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def store_token(
        redis_client, token: str, user_id: str, ttl: int = TOKEN_TTL
    ) -> None:
        redis_client.setex(f"token:{token}", ttl, user_id)

    @staticmethod
    def get_user_id_from_token(redis_client, token: str) -> Optional[str]:
        uid = redis_client.get(f"token:{token}")
        return uid.decode() if isinstance(uid, bytes) else uid

    @staticmethod
    def delete_token(redis_client, token: str) -> None:
        redis_client.delete(f"token:{token}")

    # ==================== 异步方法 ====================

    @staticmethod
    async def store_token_async(rc, token: str, uid: str, ttl: int = TOKEN_TTL) -> None:
        """异步存储 token -> user_id 映射。

        Args:
            rc: 异步 Redis 客户端
            token: 认证令牌
            uid: 用户 ID
            ttl: 过期时间（秒）
        """
        await rc.setex(f"token:{token}", ttl, uid)

    @staticmethod
    async def get_user_id_from_token_async(rc, token: str) -> Optional[str]:
        """异步从 token 获取用户 ID。

        Args:
            rc: 异步 Redis 客户端
            token: 认证令牌

        Returns:
            用户 ID，不存在或过期时返回 None
        """
        uid = await rc.get(f"token:{token}")
        return uid.decode() if isinstance(uid, bytes) else uid

    @staticmethod
    async def delete_token_async(rc, token: str) -> None:
        """异步删除 token。

        Args:
            rc: 异步 Redis 客户端
            token: 认证令牌
        """
        await rc.delete(f"token:{token}")
