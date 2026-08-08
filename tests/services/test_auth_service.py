"""测试认证服务层。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.auth_service import AuthService
from src.utils.errors import BusinessError


@pytest.fixture
def mock_user_repo():
    user_repo = AsyncMock()
    user_repo.get_user_by_account = AsyncMock(return_value=None)
    user_repo.add_user = AsyncMock()
    user_repo.update_user_token = AsyncMock()
    user_repo.get_user_by_token = AsyncMock(return_value=None)
    return user_repo


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.setex = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    return redis


@pytest.fixture
def auth_service(mock_user_repo, mock_redis):
    return AuthService(user_repo=mock_user_repo, redis_client=mock_redis)


class TestRegister:
    @pytest.mark.asyncio
    async def test_register_success(self, auth_service, mock_user_repo):
        """注册成功应返回 user_id 和 account。"""
        mock_user_repo.get_user_by_account.return_value = None
        result = await auth_service.register("test_user", "password123")
        assert "user_id" in result
        assert result["account"] == "test_user"
        mock_user_repo.add_user.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_register_duplicate_account(self, auth_service, mock_user_repo):
        """重复账号应抛出 BusinessError。"""
        mock_user_repo.get_user_by_account.return_value = MagicMock(
            id="existing", account="test_user", password="pwd"
        )
        with pytest.raises(BusinessError):
            await auth_service.register("test_user", "password123")


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_success(self, auth_service, mock_user_repo, mock_redis):
        """登录成功应返回 token 和 user_id。"""
        password_hash = "$2b$12$..."
        mock_user_repo.get_user_by_account.return_value = MagicMock(
            id="user_1",
            account="test_user",
            password=password_hash,
        )
        with (
            patch(
                "src.services.auth_service.hash_password", return_value=password_hash
            ),
            patch("src.services.auth_service.verify_password", return_value=True),
        ):
            result = await auth_service.login("test_user", "password123")
        assert "token" in result
        assert result["user_id"] == "user_1"
        mock_redis.setex.assert_called_once()
        mock_user_repo.update_user_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, auth_service, mock_user_repo):
        """错误密码应抛出 BusinessError。"""
        mock_user_repo.get_user_by_account.return_value = MagicMock(
            id="user_1",
            account="test_user",
            password="hashed_pwd",
        )
        with (
            patch("src.services.auth_service.verify_password", return_value=False),
            pytest.raises(BusinessError),
        ):
            await auth_service.login("test_user", "wrong_password")


class TestVerifyToken:
    @pytest.mark.asyncio
    async def test_verify_valid_token(self, auth_service, mock_redis):
        """有效 token 应返回 user_id。"""
        mock_redis.get.return_value = b"user_123"
        result = await auth_service.verify_token("valid_token")
        assert result == "user_123"

    @pytest.mark.asyncio
    async def test_verify_invalid_token(self, auth_service):
        """无效 token 应返回 None。"""
        result = await auth_service.verify_token("invalid_token")
        assert result is None


class TestLogout:
    @pytest.mark.asyncio
    async def test_logout_deletes_redis_key(self, auth_service, mock_redis):
        """退出登录应删除 Redis 中的 token 缓存。"""
        await auth_service.logout("some_token")
        mock_redis.delete.assert_awaited_once_with("token:some_token")
