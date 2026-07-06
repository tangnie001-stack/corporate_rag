"""对话历史管理模块 — 基于 Redis 的会话级对话缓存，支持内存降级。

本模块负责管理多轮对话的历史消息，为 RAG 链提供上下文记忆能力：
  - 优先使用 Redis 存储（支持 TTL 过期、多实例共享）
  - Redis 不可用时自动降级为内存 dict 存储（单机开发场景）
  - 支持滑动窗口（取最近 N 条消息），避免 token 溢出

在 RAG 流水线中的位置：
  用户提问 → ChatManager.get_window() 获取历史
           → RAGChain._build_prompt() 拼入 prompt
           → ChatManager.add_message() 写入本轮问答
"""

import json
from typing import Optional

import redis.asyncio as redis_async

from src.infra.db.mysql_db import MySQLDB

from loguru import logger

from src.config import MEMORY_WINDOW, REDIS_URL, REDIS_TTL


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

    def __init__(self, redis_url: Optional[str] = None, ttl: int = REDIS_TTL) -> None:
        """初始化 ChatManager。

        Args:
            redis_url: Redis 连接 URL，默认使用 config 中的全局配置
            ttl: 对话历史在 Redis 中的过期时间（秒），默认 7 天
        """
        self.ttl = ttl
        self._redis_url = redis_url or REDIS_URL
        self._redis = None
        self._in_memory: bool = False
        # 内存降级时的存储：session_id -> [msg_dict, ...]
        self._memory_store: dict[str, list[dict]] = {}
        self._mysql_db: Optional[MySQLDB] = None  # injected later for async persistence
        self._init_redis(self._redis_url)

    def set_mysql_db(self, mysql_db: MySQLDB) -> None:
        """注入 MySQLDB 实例用于异步持久化。

        在 SSE 流结束后由 _persist_conversation() 调用，
        确保 ChatManager 可以异步写入 MySQL。
        """
        self._mysql_db = mysql_db

    async def save_session_async(self, session_id: str, title: str, kb_id: str) -> None:
        """异步创建会话记录（首次消息时调用）。

        失败只记日志，不抛异常。

        Args:
            session_id: 会话 ID
            title: 会话标题（截取首条消息前 20 字）
            kb_id: 关联的知识库 ID
        """
        if self._mysql_db is None:
            return
        try:
            await self._mysql_db.create_session(session_id, title, kb_id)
        except Exception as e:
            logger.warning("Failed to save session async: {}", e)

    async def save_messages_async(
        self,
        session_id: str,
        kb_id: str,
        user_msg: str,
        assistant_msg: str,
        sources: Optional[list[str]] = None,
    ) -> None:
        """异步写入 user + assistant 消息到 MySQL。

        两次写入独立进行，失败只记日志，不抛异常。

        Args:
            session_id: 会话 ID
            kb_id: 关联的知识库 ID
            user_msg: 用户消息内容
            assistant_msg: 助理回答内容
            sources: 来源引用列表
        """
        if self._mysql_db is None:
            return
        try:
            await self._mysql_db.save_message(session_id, kb_id, "user", user_msg, None)
            await self._mysql_db.save_message(
                session_id,
                kb_id,
                "assistant",
                assistant_msg,
                sources,
            )
        except Exception as e:
            logger.warning("Failed to save messages async: {}", e)

    def cleanup_session(self, session_id: str) -> None:
        """删除 Redis 中的会话 key（尽力而为，失败不抛异常）。

        在 POST /api/sessions/delete 端点中被调用，
        确保删除会话时同时清理 Redis 缓存。
        """
        self.clear_history(session_id)

    def _init_redis(self, redis_url: str) -> None:
        """尝试连接 Redis，失败则降级为内存存储。

        先用同步 ping 验证连接是否可达，再创建异步客户端。
        """
        try:
            conn = self._get_sync_redis()
            conn.ping()
            conn.close()
            self._redis = redis_async.from_url(redis_url, decode_responses=True)
            logger.info("ChatManager: Redis async client created at {}", redis_url)
        except Exception as e:
            # Redis 不可用：静默降级为内存存储，不影响程序运行
            self._redis = None
            self._in_memory = True
            logger.warning(
                "ChatManager: Redis unavailable ({}), using InMemory fallback",
                e,
            )

    def _get_sync_redis(self):
        """创建同步 Redis 连接（用于同步方法的向后兼容）。"""
        import redis  # noqa: PLC0415

        return redis.from_url(self._redis_url, decode_responses=True)

    def _ensure_redis(self) -> None:
        """验证 Redis 连接存活，断开时自动降级为 InMemory；InMemory 期间尝试恢复 Redis。

        使用同步 Redis 连接进行健康检查（向后兼容同步方法）。
        两种场景：
          - Redis 模式：ping 检测连接，失败则尝试重连一次，重连失败降级 InMemory
          - InMemory 模式：尝试重连 Redis，成功则自动切回 Redis 模式

        Note: 此方法仅用于同步方法。异步方法使用 _ensure_redis_async。
        """
        if self._in_memory:
            # 内存模式：尝试恢复 Redis 连接（可能已重启）
            try:
                new_conn = self._get_sync_redis()
                new_conn.ping()
                new_conn.close()
                self._in_memory = False
                logger.info(
                    "ChatManager: Redis reconnected, switched back from InMemory"
                )
            except Exception:
                # Redis 仍不可用，保持 InMemory 不变
                pass
            return

        # Redis 模式：检测连接是否存活
        try:
            conn = self._get_sync_redis()
            conn.ping()
            conn.close()
        except Exception:
            # Redis 断开：尝试重连一次
            logger.warning("ChatManager: Redis ping failed, attempting reconnect...")
            try:
                conn = self._get_sync_redis()
                conn.ping()
                conn.close()
                logger.info("ChatManager: Redis reconnected")
            except Exception as e:
                logger.warning(
                    "ChatManager: Redis reconnect failed ({}), falling back to InMemory",
                    e,
                )
                self._redis = None
                self._in_memory = True

    def _session_key(self, session_id: str) -> str:
        """生成 Redis key，格式为 "chat_history:{session_id}"。

        Args:
            session_id: 会话 ID

        Returns:
            Redis key 字符串
        """
        return f"chat_history:{session_id}"

    def get_history(self, session_id: str) -> list[dict]:
        """获取指定会话的完整对话历史。

        Args:
            session_id: 会话 ID

        Returns:
            消息列表，每条为 {"role": "user"/"assistant", "content": "..."}
        """
        # 调用前检测连接状态，Redis 断开时自动降级或重连
        self._ensure_redis()
        # 内存模式：直接从 dict 读取
        if self._in_memory:
            return list(self._memory_store.get(session_id, []))

        # Redis 模式：使用同步连接读取数据
        key = self._session_key(session_id)
        try:
            conn = self._get_sync_redis()
            raw = conn.lrange(key, 0, -1)
            conn.close()
            return [json.loads(m) for m in raw]
        except Exception as e:
            logger.error("ChatManager: Redis get_history failed: {}", e)
            return []

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        sources: Optional[list] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        model_name: str = "",
    ) -> None:
        """向会话追加一条消息。

        Args:
            session_id: 会话 ID
            role: 角色（"user" 或 "assistant"）
            content: 消息文本内容
            sources: 可选的来源引用列表（assistant 回答时附带的文档引用）
            prompt_tokens: 提示 token 数
            completion_tokens: 补全 token 数
            total_tokens: 总 token 数
            model_name: 模型名称（如 qwen-max）
        """
        msg: dict = {"role": role, "content": content}
        if sources:
            msg["sources"] = sources
        # 保存 token 用量和模型名到消息 dict（用于 MySQL 持久化）
        if prompt_tokens or completion_tokens or total_tokens:
            msg["prompt_tokens"] = prompt_tokens
            msg["completion_tokens"] = completion_tokens
            msg["total_tokens"] = total_tokens
        if model_name:
            msg["model_name"] = model_name

        # 调用前检测连接状态，Redis 断开时自动降级或重连
        self._ensure_redis()
        # 内存模式：直接 append 到 dict
        if self._in_memory:
            if session_id not in self._memory_store:
                self._memory_store[session_id] = []
            self._memory_store[session_id].append(msg)
            return

        # Redis 模式：使用同步连接写入
        key = self._session_key(session_id)
        try:
            conn = self._get_sync_redis()
            conn.rpush(key, json.dumps(msg, ensure_ascii=False))
            # 每次写入都刷新过期时间，保持活跃会话不过期
            conn.expire(key, self.ttl)
            conn.close()
        except Exception as e:
            logger.error("ChatManager: Redis add_message failed: {}", e)

    def get_window(
        self,
        session_id: str,
        window_size: int = MEMORY_WINDOW,
    ) -> list[dict]:
        """获取对话历史的滑动窗口（最近 N 条消息）。

        用于构建 prompt 时限制上下文长度，避免 token 溢出。
        例如 window_size=6 时，只取最近 3 轮对话（每轮 user + assistant 各 1 条）。

        Args:
            session_id: 会话 ID
            window_size: 窗口大小（消息条数），默认 6

        Returns:
            最近 window_size 条消息的列表
        """
        history = self.get_history(session_id)
        # 取最后 N 条，如果历史不足 N 条则返回全部
        return history[-window_size:] if len(history) > window_size else history

    # ==================== 同步方法结束，异步方法开始 ====================

    async def _ensure_redis_async(self) -> None:
        """异步验证 Redis 连接存活，断开时自动降级为 InMemory。

        InMemory 期间尝试恢复 Redis，成功则自动切回 Redis 模式。
        """
        if self._in_memory:
            try:
                c = redis_async.from_url(self._redis_url, decode_responses=True)
                await c.ping()
                self._redis = c
                self._in_memory = False
            except Exception:
                pass
            return
        try:
            await self._redis.ping()
        except Exception:
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
            self.add_message(session_id, role, content, **kwargs)
            return
        msg = {"role": role, "content": content}
        key = self._session_key(session_id)
        try:
            await self._redis.rpush(key, json.dumps(msg, ensure_ascii=False))
            await self._redis.expire(key, self.ttl)
        except Exception as e:
            logger.error("add_message_async failed: {}", e)

    async def get_history_async(self, session_id: str) -> list[dict]:
        """异步获取指定会话的完整对话历史。

        Args:
            session_id: 会话 ID

        Returns:
            消息列表，每条为 {"role": "user"/"assistant", "content": "..."}
        """
        await self._ensure_redis_async()
        if self._in_memory:
            return list(self._memory_store.get(session_id, []))
        key = self._session_key(session_id)
        try:
            raw = await self._redis.lrange(key, 0, -1)
            return [json.loads(m) for m in raw]
        except Exception as e:
            logger.error("get_history_async failed: {}", e)
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
        try:
            await self._redis.delete(key)
        except Exception as e:
            logger.error("clear_history_async failed: {}", e)

    def clear_history(self, session_id: str) -> None:
        """清空指定会话的所有对话历史。

        Args:
            session_id: 会话 ID
        """
        # 调用前检测连接状态，Redis 断开时自动降级或重连
        self._ensure_redis()
        # 内存模式：从 dict 中移除该 session
        if self._in_memory:
            self._memory_store.pop(session_id, None)
            return

        # Redis 模式：使用同步连接删除
        key = self._session_key(session_id)
        try:
            conn = self._get_sync_redis()
            conn.delete(key)
            conn.close()
        except Exception as e:
            logger.error("ChatManager: Redis clear_history failed: {}", e)
