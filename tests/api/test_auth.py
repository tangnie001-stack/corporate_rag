"""Auth 端点测试 — login / verify / logout / anonymous。"""

from unittest.mock import AsyncMock

from src.utils.errors import BusinessError


# ─── Login ───


def test_login_new_user_auto_register(mock_app_service, client):
    """新用户自动注册并返回 token。"""
    mock_svc = mock_app_service
    mock_svc.auth_service = AsyncMock()
    mock_svc.auth_service.register = AsyncMock(
        return_value={"user_id": "new-uuid", "account": "newuser"}
    )
    mock_svc.auth_service.login = AsyncMock(
        return_value={"token": "test-token", "user_id": "new-uuid"}
    )

    response = client.post(
        "/api/auth/login", json={"account": "newuser", "password": "pass123"}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["token"] == "test-token"
    assert len(data["user_id"]) > 0
    mock_svc.auth_service.register.assert_called_once_with("newuser", "pass123")
    mock_svc.auth_service.login.assert_called_once_with("newuser", "pass123")


def test_login_existing_user_correct_password(mock_app_service, client):
    """已有用户，密码正确，返回 token。"""
    mock_svc = mock_app_service
    mock_svc.auth_service = AsyncMock()
    mock_svc.auth_service.register = AsyncMock(
        side_effect=BusinessError("ACCOUNT_EXISTS", "账号已存在")
    )
    mock_svc.auth_service.login = AsyncMock(
        return_value={"token": "test-token", "user_id": "u1"}
    )

    response = client.post(
        "/api/auth/login", json={"account": "existing", "password": "pass123"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["token"] == "test-token"
    mock_svc.auth_service.register.assert_called_once_with("existing", "pass123")
    mock_svc.auth_service.login.assert_called_once_with("existing", "pass123")


def test_login_wrong_password(mock_app_service, client):
    """密码错误返回 400（BusinessError 默认状态码）。"""
    mock_svc = mock_app_service
    mock_svc.auth_service = AsyncMock()
    mock_svc.auth_service.register = AsyncMock(
        side_effect=BusinessError("ACCOUNT_EXISTS", "账号已存在")
    )
    mock_svc.auth_service.login = AsyncMock(
        side_effect=BusinessError("WRONG_PASSWORD", "密码错误")
    )

    response = client.post(
        "/api/auth/login", json={"account": "existing", "password": "wrong"}
    )

    assert response.status_code == 400


def test_login_missing_password(client):
    """缺 password 字段返回 422。"""
    response = client.post("/api/auth/login", json={"account": "test"})
    assert response.status_code == 422


# ─── Verify ───


def test_verify_token_valid(mock_app_service, client):
    """有效 token 返回 valid=True + user_id。"""
    mock_svc = mock_app_service
    mock_svc.auth_service = AsyncMock()
    mock_svc.auth_service.verify_token = AsyncMock(return_value="u1")

    client.cookies.set("token", "valid-token")
    response = client.post("/api/auth/verify")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    assert data["user_id"] == "u1"


def test_verify_no_token(mock_app_service, client):
    """无 Cookie 时返回 valid=False。"""
    mock_svc = mock_app_service
    mock_svc.auth_service = AsyncMock()
    mock_svc.auth_service.verify_token = AsyncMock(return_value=None)

    response = client.post("/api/auth/verify")

    assert response.status_code == 200
    assert response.json()["data"]["valid"] is False


# ─── Logout ───


def test_logout(mock_app_service, client):
    """退出登录清除 token。"""
    mock_svc = mock_app_service
    mock_svc.auth_service = AsyncMock()
    mock_svc.auth_service.logout = AsyncMock()

    client.cookies.set("token", "test-token")
    response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert response.json()["data"]["message"] == "已退出登录"
    mock_svc.auth_service.logout.assert_called_once_with("test-token")


# ─── Anonymous ───


def test_anonymous_new_user(client):
    """无 Cookie 时生成新匿名 ID。"""
    response = client.post("/api/auth/anonymous")
    assert response.status_code == 200
    assert len(response.json()["data"]["user_id"]) == 36


def test_anonymous_existing_user(client):
    """已有匿名 Cookie 时返回已有 ID。"""
    client.cookies.set("user_id", "fixed-uuid-0000-0000")
    response = client.post("/api/auth/anonymous")
    assert response.status_code == 200
    assert response.json()["data"]["user_id"] == "fixed-uuid-0000-0000"
