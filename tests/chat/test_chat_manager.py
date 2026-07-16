"""ChatManager 对话管理的单元测试。

测试目标：
- Redis 不可用时自动降级到内存存储（InMemory fallback）
- 消息添加 / 历史查询 / 窗口截取 / 清空历史
- 会话隔离（不同 session_id 互不干扰）
- MEMORY_WINDOW 配置生效
- Redis 断线自动降级 → 恢复后自动重连

注意：基础功能测试使用不存在的 Redis 端口 (16379) 来强制触发 InMemory 降级。
重连测试通过 mock redis.from_url / ping 来模拟 Redis 的断开与恢复。
"""

from unittest.mock import MagicMock, patch

import pytest
from src.chat.manager import ChatManager
from src.config import MEMORY_WINDOW


class TestChatManagerInMemory:
    """测试 Redis 不可用时的 InMemory 降级模式。"""

    @pytest.fixture
    def cm(self):
        """创建指向不存在端口的 ChatManager，强制 InMemory 降级。"""
        return ChatManager(redis_url="redis://localhost:16379/0")  # 不存在的端口

    def test_init_fallback_to_inmemory(self, cm):
        """连接失败时应设置 _in_memory 标志为 True。"""
        assert cm._in_memory is True

    def test_add_and_get_history(self, cm):
        """基本操作：添加消息并按顺序查询历史。"""
        session_id = "test_session_1"
        cm.add_message(session_id, "user", "你好")
        cm.add_message(session_id, "assistant", "你好！有什么可以帮助你的？")

        history = cm.get_history(session_id)
        assert len(history) == 2
        assert history[0]["role"] == "user"  # 用户消息在前
        assert history[0]["content"] == "你好"
        assert history[1]["role"] == "assistant"  # 助手消息在后

    def test_get_window_limits(self, cm):
        """滑动窗口截取：只返回最后 N 条消息。"""
        session_id = "test_window"
        for i in range(10):
            cm.add_message(session_id, "user", f"msg_{i}")

        # 窗口大小为 3，应返回最后 3 条
        window = cm.get_window(session_id, window_size=3)
        assert len(window) == 3
        assert window[-1]["content"] == "msg_9"  # 最后一条是 msg_9

    def test_clear_history(self, cm):
        """清空指定会话的历史记录。"""
        session_id = "test_clear"
        cm.add_message(session_id, "user", "hello")
        cm.clear_history(session_id)
        assert cm.get_history(session_id) == []

    def test_get_window_default_memory_window(self, cm):
        """未指定 window_size 时应使用 config.MEMORY_WINDOW 默认值。"""
        session_id = "test_default_window"
        # 插入超过 MEMORY_WINDOW 的消息数
        for i in range(MEMORY_WINDOW + 5):
            cm.add_message(session_id, "user", f"msg_{i}")

        window = cm.get_window(session_id)  # 不传 window_size
        assert len(window) <= MEMORY_WINDOW  # 不应超过配置的窗口大小

    def test_get_history_empty_session(self, cm):
        """查询不存在的会话应返回空列表。"""
        assert cm.get_history("nonexistent_session") == []

    def test_inmemory_store_isolation(self, cm):
        """会话隔离：不同 session_id 的消息互不干扰。"""
        cm.add_message("session_a", "user", "from_a")
        cm.add_message("session_b", "user", "from_b")
        hist_a = cm.get_history("session_a")
        hist_b = cm.get_history("session_b")
        assert len(hist_a) == 1
        assert len(hist_b) == 1
        assert hist_a[0]["content"] == "from_a"
        assert hist_b[0]["content"] == "from_b"


class TestChatManagerRedisReconnection:
    """测试 Redis 断开后的自动降级和恢复重连。

    通过 mock 控制 redis.from_url / ping 的行为来模拟各种 Redis 状态：
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
            client.ping.return_value = True
        else:
            client.ping.side_effect = ConnectionError("Redis disconnected")
        return client

    @patch("redis.from_url")
    def test_redis_initially_ok_then_failover_to_inmemory(
        self,
        mock_from_url: MagicMock,
    ):
        """Redis 运行中突然断开，下次操作应自动降级为 InMemory。"""
        # 第一次 from_url 返回正常的客户端（用于 _init_redis 的同步 ping）
        mock_from_url.return_value = self._mock_redis_client(ping_ok=True)

        cm = ChatManager(redis_url="redis://localhost:6379/0")
        assert cm._in_memory is False  # 初始连接正常

        # 让后续 from_url 返回失败的客户端（模拟 Redis 断开）
        mock_from_url.side_effect = lambda url, **kw: self._mock_redis_client(
            ping_ok=False
        )

        # 触发 get_history → _ensure_redis() → _get_sync_redis() → ping 失败 → 降级
        history = cm.get_history("test_session")
        assert cm._in_memory is True
        # 降级后 InMemory 应正常返回空结果
        assert history == []

    @patch("redis.from_url")
    def test_redis_recovers_from_inmemory(self, mock_from_url: MagicMock):
        """InMemory 降级后 Redis 恢复，下次操作应自动切回 Redis 模式。"""
        # 第一次 from_url：给一个能用的客户端
        working_client = self._mock_redis_client(ping_ok=True)
        mock_from_url.return_value = working_client

        cm = ChatManager(redis_url="redis://localhost:6379/0")
        assert cm._in_memory is False

        # 模拟 Redis 断开：让 ping 抛异常
        working_client.ping.side_effect = ConnectionError("Redis disconnected")

        # 触发降级
        cm.get_history("sess")
        assert cm._in_memory is True

        # 模拟 Redis 恢复：新的 from_url 返回正常客户端
        recovered_client = self._mock_redis_client(ping_ok=True)
        mock_from_url.side_effect = lambda url, **kw: recovered_client

        # 再次调用 → _ensure_redis() 应重连成功 → 切回 Redis 模式
        cm.get_history("sess")
        assert cm._in_memory is False

    @patch("redis.from_url")
    def test_add_message_after_redis_failover(
        self,
        mock_from_url: MagicMock,
    ):
        """Redis 断开后 add_message 应正常写入 InMemory 而不是崩溃。"""
        working_client = self._mock_redis_client(ping_ok=True)
        mock_from_url.return_value = working_client

        cm = ChatManager(redis_url="redis://localhost:6379/0")

        # 模拟 Redis 断开
        working_client.ping.side_effect = ConnectionError("Redis disconnected")
        mock_from_url.side_effect = lambda url, **kw: self._mock_redis_client(
            ping_ok=False
        )

        # 写入消息（应降级到 InMemory）
        cm.add_message("sess", "user", "测试消息")
        history = cm.get_history("sess")
        assert cm._in_memory is True
        assert len(history) == 1
        assert history[0]["content"] == "测试消息"


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
        assert h[0]["content"] == "hello"
        assert h[0]["role"] == "user"

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
        assert len(h_a) == 1 and h_a[0]["content"] == "from_a"
        assert len(h_b) == 1 and h_b[0]["content"] == "from_b"
