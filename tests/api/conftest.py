"""API 测试公共基础 — TestClient + auth 辅助函数。"""

import redis
import redis.asyncio as redis_async
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from src.main import app
from src.config import REDIS_URL
from tests.api.test_config import TEST_TOKEN, TEST_USER_ID

# token 有效期（秒），足够覆盖单次测试执行
_TOKEN_TTL = 60


@pytest.fixture
def client() -> TestClient:
    """返回裸 TestClient（无认证）。"""
    return TestClient(app)


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    """返回带认证 Cookie 的 TestClient。

    将测试 token 预存入 Redis，使中间件能走通完整的 token 校验流程。
    适用于 kb / documents / sessions 等需要登录态的端点。
    """
    # 同步 Redis：fixture setup/teardown
    rc = redis.from_url(REDIS_URL, decode_responses=True)
    rc.set(f"token:{TEST_TOKEN}", TEST_USER_ID, ex=_TOKEN_TTL)

    # patch 中间件使用的 get_redis_client，返回新连接避免事件循环冲突
    patcher = patch(
        "src.middleware.auth.get_redis_client",
        side_effect=lambda: redis_async.from_url(REDIS_URL, decode_responses=True),
    )
    patcher.start()

    client.cookies.set("token", TEST_TOKEN)
    yield client
    client.cookies.clear()

    patcher.stop()
    rc.delete(f"token:{TEST_TOKEN}")
    rc.close()


@pytest.fixture
def mock_app_service():
    """替换 get_app_service 依赖，返回可配置的 AsyncMock。

    每个测试可通过此 fixture 配置 AppService 各方法的返回值。
    测试结束后自动清理 dependency_overrides。
    """
    from src.api.dependencies import get_app_service

    mock = AsyncMock()
    app.dependency_overrides[get_app_service] = lambda: mock
    yield mock
    app.dependency_overrides.clear()
