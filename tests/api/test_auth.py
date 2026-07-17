"""Auth 端点测试 — login / verify / logout / anonymous。"""

from unittest.mock import AsyncMock, MagicMock, patch

from tests.api.mock_data import make_user


# ─── Login ───


@patch("src.api.auth.UserAuth.hash_password", return_value="hashed_pwd")
def test_login_new_user_auto_register(mock_hash, mock_app_service, client):
    """新用户自动注册并返回 token。"""
    mock_svc = mock_app_service
    mock_svc.db.get_user_by_account = AsyncMock(return_value=None)
    mock_svc.db.add_user = AsyncMock()
    mock_svc.db.update_user_token = AsyncMock()
    mock_svc.redis_client = MagicMock()

    with patch("src.api.auth.UserAuth.generate_token", return_value="test-token"):
        with patch(
            "src.api.auth.UserAuth.store_token_async", new_callable=AsyncMock
        ):
            response = client.post(
                "/api/auth/login", json={"account": "newuser", "password": "pass123"}
            )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["token"] == "test-token"
    assert len(data["user_id"]) > 0


@patch("src.api.auth.UserAuth.hash_password", return_value="correct_hash")
def test_login_existing_user_correct_password(mock_hash, mock_app_service, client):
    """已有用户，密码正确，返回 token。"""
    mock_svc = mock_app_service
    mock_svc.db.get_user_by_account = AsyncMock(
        return_value=make_user("u1", "existing", "correct_hash")
    )
    mock_svc.db.update_user_token = AsyncMock()
    mock_svc.redis_client = MagicMock()

    with patch("src.api.auth.UserAuth.generate_token", return_value="test-token"):
        with patch(
            "src.api.auth.UserAuth.store_token_async", new_callable=AsyncMock
        ):
            response = client.post(
                "/api/auth/login", json={"account": "existing", "password": "pass123"}
            )

    assert response.status_code == 200
    assert response.json()["data"]["token"] == "test-token"


@patch("src.api.auth.UserAuth.hash_password", return_value="wrong_hash")
def test_login_wrong_password(mock_hash, mock_app_service, client):
    """密码错误返回 401。"""
    mock_svc = mock_app_service
    mock_svc.db.get_user_by_account = AsyncMock(
        return_value=make_user("u1", "existing", "correct_hash")
    )

    response = client.post(
        "/api/auth/login", json={"account": "existing", "password": "wrong"}
    )

    assert response.status_code == 401


def test_login_missing_password(client):
    """缺 password 字段返回 422。"""
    response = client.post("/api/auth/login", json={"account": "test"})
    assert response.status_code == 422


# ─── Verify ───


@patch(
    "src.api.auth.UserAuth.get_user_id_from_token_async",
    new_callable=AsyncMock,
    return_value="u1",
)
def test_verify_token_valid(mock_get_uid, mock_app_service, client):
    """有效 token 返回 valid=True + user_id。"""
    client.cookies.set("token", "valid-token")
    response = client.post("/api/auth/verify")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    assert data["user_id"] == "u1"


def test_verify_no_token(mock_app_service, client):
    """无 Cookie 时返回 valid=False。"""
    response = client.post("/api/auth/verify")
    assert response.status_code == 200
    assert response.json()["data"]["valid"] is False


# ─── Logout ───


@patch("src.api.auth.UserAuth.delete_token_async", new_callable=AsyncMock)
def test_logout(mock_delete, mock_app_service, client):
    """退出登录清除 token。"""
    client.cookies.set("token", "test-token")
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    assert response.json()["data"]["message"] == "已退出登录"


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
