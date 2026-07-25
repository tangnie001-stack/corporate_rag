"""Tests for auth service layer."""

import pytest
from unittest.mock import AsyncMock, patch

from src.services.auth_service import AuthService
from src.utils.errors import BusinessError


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get_user_by_account = AsyncMock(return_value=None)
    db.add_user = AsyncMock()
    db.update_user_token = AsyncMock()
    db.get_user_by_token = AsyncMock(return_value=None)
    return db


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.setex = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    return redis


@pytest.fixture
def auth_service(mock_db, mock_redis):
    return AuthService(db=mock_db, redis_client=mock_redis)


class TestRegister:
    @pytest.mark.asyncio
    async def test_register_success(self, auth_service, mock_db):
        """Register success returns user_id and account."""
        mock_db.get_user_by_account.return_value = None
        result = await auth_service.register("test_user", "password123")
        assert "user_id" in result
        assert result["account"] == "test_user"
        mock_db.add_user.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_register_duplicate_account(self, auth_service, mock_db):
        """Duplicate account raises BusinessError."""
        mock_db.get_user_by_account.return_value = {
            "id": "existing",
            "account": "test_user",
        }
        with pytest.raises(BusinessError):
            await auth_service.register("test_user", "password123")


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_success(self, auth_service, mock_db, mock_redis):
        """Login success returns token and user_id."""
        password_hash = "$2b$12$..."
        mock_db.get_user_by_account.return_value = {
            "id": "user_1",
            "account": "test_user",
            "password": password_hash,
        }
        with patch(
            "src.services.auth_service.hash_password", return_value=password_hash
        ):
            with patch("src.services.auth_service.verify_password", return_value=True):
                result = await auth_service.login("test_user", "password123")
        assert "token" in result
        assert result["user_id"] == "user_1"
        mock_redis.setex.assert_called_once()
        mock_db.update_user_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, auth_service, mock_db):
        """Wrong password raises BusinessError."""
        mock_db.get_user_by_account.return_value = {
            "id": "user_1",
            "account": "test_user",
            "password": "hashed_pwd",
        }
        with patch("src.services.auth_service.verify_password", return_value=False):
            with pytest.raises(BusinessError):
                await auth_service.login("test_user", "wrong_password")


class TestVerifyToken:
    @pytest.mark.asyncio
    async def test_verify_valid_token(self, auth_service, mock_redis):
        """Valid token returns user_id."""
        mock_redis.get.return_value = b"user_123"
        result = await auth_service.verify_token("valid_token")
        assert result == "user_123"

    @pytest.mark.asyncio
    async def test_verify_invalid_token(self, auth_service):
        """Invalid token returns None."""
        result = await auth_service.verify_token("invalid_token")
        assert result is None


class TestLogout:
    @pytest.mark.asyncio
    async def test_logout_deletes_redis_key(self, auth_service, mock_redis):
        """Logout deletes Redis token cache."""
        await auth_service.logout("some_token")
        mock_redis.delete.assert_awaited_once_with("token:some_token")
