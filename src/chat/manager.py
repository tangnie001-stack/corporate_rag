"""对话历史管理器 — Redis 优先，InMemory 降级。

本模块负责管理多轮对话的历史消息，为 RAG 链提供上下文记忆能力：
  - 优先使用 Redis 存储（支持 TTL 过期、多实例共享）
  - Redis 不可用时自动降级为内存 dict 存储（单机开发场景）
  - 支持滑动窗口（取最近 N 条消息），避免 token 溢出

在 RAG 流水线中的位置：
  用户提问 → ChatManager.get_history_async() 获取历史
           → RAGChain._build_prompt() 拼入 prompt
           → ChatManager.add_message_async() 写入本轮问答
"""

import json

import redis as redis_sync
import redis.asyncio as redis_async
from loguru import logger

from src.chat.persistence import PersistenceService
from src.config import REDIS_TTL, REDIS_URL
from src.infra.db.mysql_db import ChatRepo
from src.infra.llm.chat_message import ChatMessage


class ChatManager:
    """对话历史管理器 — Redis 优先，内存降级。

    构造时尝试连接 Redis，连接失败则静默降级为内存存储（dict）。
    内存模式下数据仅在当前进程存活，重启后丢失，适合本地开发调试。
    Redis 模式下数据持久化，支持多实例共享同一会话历史。

    Redis 数据结构：
      Key:   "chat_history:{session_id}"
      Type:  List（每条元素为 JSON 序列化的消息 dict）
      TTL:   默认 7 天（可通过 REDIS_TTL 配置）
    """

    def __init__(
        self,
        redis_url: str | None = None,
        ttl: int = REDIS_TTL,
    ) -> None:
        """初始化 ChatManager。

        Args:
            redis_url: Redis 连接 URL，默认使用 config 中的全局配置
            ttl: 对话历史在 Redis 中的过期时间（秒），默认 7 天
        """
        self.ttl = ttl
        self._redis_url = redis_url or REDIS_URL
        self._redis: redis_async.Redis | None = None
        self._in_memory: bool = False
        # 内存降级时的存储：session_id -> [msg_dict, ...]
        self._memory_store: dict[str, list[dict]] = {}
        self._persistence: PersistenceService | None = None
        self._init_redis(self._redis_url)

    def set_chat_repo(self, chat_repo: ChatRepo) -> None:
        """注入 ChatRepo 实例（包装为 PersistenceService）。

        在请求开始时由 chat_stream 调用（M1 user 落库前置），
        确保 ChatManager 的 save_user_async / save_assistant_async
        可以异步写入 MySQL。
        """
        self._persistence = PersistenceService(chat_repo)

    # ═══════════ 异步持久化（委托给 PersistenceService） ═══════════

    async def save_session_async(
        self,
        session_id: str,
        title: str,
        kb_id: str,
        user_id: str = "",
    ) -> None:
        """异步创建会话记录（首次消息时调用）。

        失败只记日志，不抛异常。
        若未注入 MySQLDB，则静默跳过。

        Args:
            session_id: 会话 ID
            title: 会话标题（截取首条消息前 20 字）
            kb_id: 关联的知识库 ID
            user_id: 所属用户 ID
        """
        if self._persistence:
            await self._persistence.save_session(session_id, title, kb_id, user_id)

    async def save_messages_async(
        self,
        session_id: str,
        kb_id: str,
        user_msg: str,
        assistant_msg: str,
        sources: list[str] | None = None,
    ) -> None:
        """异步写入 user + assistant 消息到 MySQL。

        两次写入独立进行，失败只记日志，不抛异常。
        若未注入 MySQLDB，则静默跳过。

        Args:
            session_id: 会话 ID
            kb_id: 关联的知识库 ID
            user_msg: 用户消息内容
            assistant_msg: 助理回答内容
            sources: 来源引用列表
        """
        if self._persistence:
            await self._persistence.save_messages(
                session_id,
                kb_id,
                user_msg,
                assistant_msg,
                sources,
            )

    async def save_user_async(self, session_id: str, kb_id: str, user_msg: str) -> None:
        """异步写入单条 user 消息到 MySQL（请求开始时调用）。"""
        if self._persistence:
            await self._persistence.save_user_message(session_id, kb_id, user_msg)

    async def save_assistant_async(
        self,
        session_id: str,
        kb_id: str,
        assistant_msg: str,
        sources: list[str] | None = None,
        status: str = "complete",
    ) -> None:
        """异步写入单条 assistant 消息到 MySQL（完成/中止时调用）。"""
        if self._persistence:
            await self._persistence.save_assistant_message(
                session_id, kb_id, assistant_msg, sources, status
            )

    # ═══════════ Redis / InMemory 核心 ═══════════

    def _init_redis(self, redis_url: str) -> None:
        """尝试连接 Redis，失败则降级为内存存储。

        先用同步 ping 验证连接是否可达，再创建异步客户端。
        """
        try:
            conn = redis_sync.from_url(redis_url, decode_responses=True)
            conn.ping()
            conn.close()
            self._redis = redis_async.from_url(redis_url, decode_responses=True)
            logger.info("ChatManager: Redis async client created at {}", redis_url)
        except Exception as e:  # noqa: BLE001
            # Redis 不可用：静默降级为内存存储，不影响程序运行
            self._redis = None
            self._in_memory = True
            logger.warning(
                "ChatManager: Redis unavailable ({}), using InMemory fallback",
                e,
            )

    def _session_key(self, session_id: str) -> str:
        """生成 Redis key，格式为 "chat_history:{session_id}"。

        Args:
            session_id: 会话 ID

        Returns:
            Redis key 字符串
        """
        return f"chat_history:{session_id}"

    # ═══════════ 异步方法 ═══════════

    async def _ensure_redis_async(self) -> None:
        """异步验证 Redis 连接存活，断开时自动降级为 InMemory。

        InMemory 期间尝试恢复 Redis，成功则自动切回 Redis 模式。
        双检锁避免协程切换期间重复创建 Redis 连接。
        """
        if self._in_memory:
            try:
                c = redis_async.from_url(self._redis_url, decode_responses=True)
                await c.ping()
                # 双检：await 期间可能另一个协程已恢复
                if not self._in_memory:
                    await c.close()
                    return
                self._redis = c
                self._in_memory = False
            except Exception:  # noqa: BLE001, S110
                pass
            return
        assert self._redis is not None
        try:
            await self._redis.ping()
        except Exception:  # noqa: BLE001
            # 双检：await 期间可能另一个协程已降级
            if self._in_memory:
                return
            self._redis = None
            self._in_memory = True

    async def add_message_async(
        self,
        session_id: str,
        role: str,
        content: str,
        **kwargs,
    ) -> None:
        """异步向会话追加一条消息。

        Args:
            session_id: 会话 ID
            role: 角色（"user" 或 "assistant"）
            content: 消息文本内容
        """
        await self._ensure_redis_async()
        if self._in_memory:
            msg = {"role": role, "content": content}
            if session_id not in self._memory_store:
                self._memory_store[session_id] = []
            self._memory_store[session_id].append(msg)
            return
        msg = {"role": role, "content": content}
        key = self._session_key(session_id)
        assert self._redis is not None
        try:
            await self._redis.rpush(key, json.dumps(msg, ensure_ascii=False))
            await self._redis.expire(key, self.ttl)
        except Exception as e:  # noqa: BLE001
            logger.warning("add_message_async failed: {}", e)

    async def get_history_async(self, session_id: str) -> list[ChatMessage]:
        """异步获取指定会话的完整对话历史。

        Args:
            session_id: 会话 ID

        Returns:
            消息列表，每条为 ChatMessage
        """
        await self._ensure_redis_async()
        if self._in_memory:
            return [
                ChatMessage(**msg) for msg in self._memory_store.get(session_id, [])
            ]
        key = self._session_key(session_id)
        assert self._redis is not None
        try:
            raw = await self._redis.lrange(key, 0, -1)
            return [ChatMessage(**json.loads(m)) for m in raw]
        except Exception as e:  # noqa: BLE001
            logger.warning("get_history_async failed: {}", e)
            return []

    async def clear_history_async(self, session_id: str) -> None:
        """异步清空指定会话的所有对话历史。

        Args:
            session_id: 会话 ID
        """
        await self._ensure_redis_async()
        if self._in_memory:
            self._memory_store.pop(session_id, None)
            return
        key = self._session_key(session_id)
        assert self._redis is not None
        try:
            await self._redis.delete(key)
        except Exception as e:  # noqa: BLE001
            logger.warning("clear_history_async failed: {}", e)
