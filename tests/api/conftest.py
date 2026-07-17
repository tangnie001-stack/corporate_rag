"""API 测试公共基础 — TestClient + auth 辅助函数。"""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from src.main import app


@pytest.fixture
def client() -> TestClient:
    """返回裸 TestClient（无认证）。"""
    return TestClient(app)


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    """返回带认证 Cookie 的 TestClient。

    自动 patch 中间件的 token 校验，模拟 'test-user-id' 用户已登录的状态。
    适用于 kb / documents / sessions 等需要登录态的端点。
    """
    client.cookies.set("token", "test-token")
    patcher = patch(
        "src.middleware.auth.UserAuth.get_user_id_from_token_async",
        new_callable=AsyncMock,
        return_value="test-user-id",
    )
    patcher.start()
    yield client
    patcher.stop()
    client.cookies.clear()
