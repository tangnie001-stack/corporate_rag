"""ChatManager 对话管理的单元测试。

测试目标：
- Redis 不可用时自动降级到内存存储（InMemory fallback）
- 消息添加 / 历史查询 / 窗口截取 / 清空历史
- 会话隔离（不同 session_id 互不干扰）
- MEMORY_WINDOW 配置生效
- Redis 断线自动降级 → 恢复后自动重连

注意：基础功能测试使用不存在的 Redis 端口 (16379) 来强制触发 InMemory 降级。
重连测试通过 mock redis.from_url / redis.asyncio.from_url 来模拟 Redis 的断开与恢复。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.chat.manager import ChatManager


class TestChatManagerInMemory:
    """测试 Redis 不可用时的 InMemory 降级模式。"""

    @pytest.fixture
    def cm(self):
        """创建指向不存在端口的 ChatManager，强制 InMemory 降级。"""
        return ChatManager(redis_url="redis://localhost:16379/0")  # 不存在的端口

    def test_init_fallback_to_inmemory(self, cm):
        """连接失败时应设置 _in_memory 标志为 True。"""
        assert cm._in_memory is True


class TestChatManagerRedisReconnection:
    """测试 Redis 断开后的自动降级和恢复重连。

    通过 mock 控制 redis.from_url / redis.asyncio.from_url 的行为来模拟各种 Redis 状态：
      - 初始连接正常 → 中途 ping 失败 → 自动降级 InMemory
      - InMemory 期间 Redis 恢复 → 自动切回 Redis 模式
    """

    def _mock_redis_client(self, ping_ok: bool = True) -> MagicMock:
        """创建一个模拟的 Redis 客户端，可控制 ping 的成功与否。

        Args:
            ping_ok: True 时 ping 正常返回，False 时抛异常

        Returns:
            配置好的 MagicMock 实例
        """
        client = MagicMock()
        if ping_ok:
            client.ping = AsyncMock(return_value=True)
        else:
            client.ping = AsyncMock(side_effect=ConnectionError("Redis disconnected"))
        return client

    @patch("redis.from_url")
    @patch("redis.asyncio.from_url")
    @pytest.mark.asyncio
    async def test_redis_initially_ok_then_failover_to_inmemory(
        self,
        mock_async_from_url: MagicMock,
        mock_from_url: MagicMock,
    ):
        """Redis 运行中突然断开，下次操作应自动降级为 InMemory。"""
        working_client = self._mock_redis_client(ping_ok=True)
        mock_from_url.return_value = working_client
        mock_async_from_url.return_value = working_client

        cm = ChatManager(redis_url="redis://localhost:6379/0")
        assert cm._in_memory is False  # 初始连接正常

        # 让缓存的 Redis client ping 失败（模拟 Redis 断开）
        working_client.ping = AsyncMock(
            side_effect=ConnectionError("Redis disconnected")
        )

        # 触发 get_history_async → _ensure_redis_async → ping 失败 → 降级
        history = await cm.get_history_async("test_session")
        assert cm._in_memory is True
        # 降级后 InMemory 应正常返回空结果
        assert history == []

    @patch("redis.from_url")
    @patch("redis.asyncio.from_url")
    @pytest.mark.asyncio
    async def test_redis_recovers_from_inmemory(
        self,
        mock_async_from_url: MagicMock,
        mock_from_url: MagicMock,
    ):
        """InMemory 降级后 Redis 恢复，下次操作应自动切回 Redis 模式。"""
        working_client = self._mock_redis_client(ping_ok=True)
        mock_from_url.return_value = working_client
        mock_async_from_url.return_value = working_client

        cm = ChatManager(redis_url="redis://localhost:6379/0")
        assert cm._in_memory is False

        # 模拟 Redis 断开：缓存 client 的 ping 失败
        working_client.ping = AsyncMock(
            side_effect=ConnectionError("Redis disconnected")
        )

        # 触发降级
        await cm.get_history_async("sess")
        assert cm._in_memory is True

        # 模拟 Redis 恢复：async from_url 返回正常客户端
        recovered_client = self._mock_redis_client(ping_ok=True)
        recovered_client.lrange = AsyncMock(return_value=[])
        mock_async_from_url.side_effect = lambda url, **kw: recovered_client

        # 再次调用 → _ensure_redis_async 应重连成功 → 切回 Redis 模式
        await cm.get_history_async("sess")
        assert cm._in_memory is False

    @patch("redis.from_url")
    @patch("redis.asyncio.from_url")
    @pytest.mark.asyncio
    async def test_add_message_after_redis_failover(
        self,
        mock_async_from_url: MagicMock,
        mock_from_url: MagicMock,
    ):
        """Redis 断开后 add_message_async 应正常写入 InMemory 而不是崩溃。"""
        working_client = self._mock_redis_client(ping_ok=True)
        mock_from_url.return_value = working_client
        mock_async_from_url.return_value = working_client

        cm = ChatManager(redis_url="redis://localhost:6379/0")

        # 模拟 Redis 断开：缓存 client 的 ping 失败
        working_client.ping = AsyncMock(
            side_effect=ConnectionError("Redis disconnected")
        )

        # 写入消息（应降级到 InMemory）
        await cm.add_message_async("sess", "user", "测试消息")
        history = await cm.get_history_async("sess")
        assert cm._in_memory is True
        assert len(history) == 1
        assert history[0].content == "测试消息"


class TestChatManagerAsync:
    """测试异步 Redis 方法（InMemory 降级模式）。"""

    @pytest.fixture
    def cm(self):
        """创建指向不存在端口的 ChatManager，强制 InMemory 降级。"""
        return ChatManager(redis_url="redis://localhost:16379/0")

    @pytest.mark.asyncio
    async def test_async_in_memory_add_and_get(self, cm):
        """异步 InMemory 模式：添加消息并查询历史。"""
        await cm.add_message_async("s1", "user", "hello")
        h = await cm.get_history_async("s1")
        assert len(h) == 1
        assert h[0].content == "hello"
        assert h[0].role == "user"

    @pytest.mark.asyncio
    async def test_async_in_memory_clear(self, cm):
        """异步 InMemory 模式：清空历史。"""
        await cm.add_message_async("s1", "user", "hello")
        await cm.clear_history_async("s1")
        h = await cm.get_history_async("s1")
        assert h == []

    @pytest.mark.asyncio
    async def test_async_in_memory_empty_session(self, cm):
        """异步 InMemory 模式：查询不存在的会话应返回空列表。"""
        h = await cm.get_history_async("nonexistent")
        assert h == []

    @pytest.mark.asyncio
    async def test_async_in_memory_isolation(self, cm):
        """异步 InMemory 模式：会话隔离，不同 session_id 互不干扰。"""
        await cm.add_message_async("s_a", "user", "from_a")
        await cm.add_message_async("s_b", "user", "from_b")
        h_a = await cm.get_history_async("s_a")
        h_b = await cm.get_history_async("s_b")
        assert len(h_a) == 1 and h_a[0].content == "from_a"
        assert len(h_b) == 1 and h_b[0].content == "from_b"
