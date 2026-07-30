"""Langfuse 追踪器 — 基于官方 Python SDK 的封装层。

提供与原有 REST API 版本一致的接口（start_trace / end_trace /
start_generation / end_generation），使消费方（rag_chain.py）零改动。
延迟初始化，配置缺失或初始化失败时静默降级。
"""

from dataclasses import dataclass, asdict
import functools
import inspect
from typing import Optional, cast

from loguru import logger

from langfuse import Langfuse
from langfuse.model import ModelUsage

from src.infra.llm.trace_context import current_trace_id, current_tracer
from src.infra.llm.token_usage import TokenUsage


@dataclass(frozen=True)
class TraceInput:
    """Tracing 输入数据 — 用于 start_trace 的统一输入结构。

    使用 dataclass 替代手动构造 dict，消除 key 名与变量名的重复维护。
    """

    kb_id: str  # 知识库 ID（空字符串 = 跨库搜索）
    session_id: str  # 会话 ID（用于 Langfuse 会话聚合）
    query: str  # 用户查询文本


def traced(name: str):
    """装饰 async generator 方法，自动管理 trace 生命周期。

    装饰器在编译期检查方法签名，自动提取 kb_id / session_id / query
    参数构造 TraceInput。方法体只需关注业务逻辑，无需调用
    start_trace / end_trace。

    Args:
        name: trace 名称（如 "chat_stream_agent"）

    Usage:
        @traced("chat_stream_agent")
        async def stream_chat(self, kb_id, session_id, query):
            ...  # 无需 start_trace / end_trace
    """
    TRACE_INPUT_FIELDS = ("kb_id", "session_id", "query")

    def decorator(func):
        sig = inspect.signature(func)
        has_input = all(f in sig.parameters for f in TRACE_INPUT_FIELDS)

        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            input_data = None
            if has_input:
                bound = sig.bind(self, *args, **kwargs)
                bound.apply_defaults()
                input_data = TraceInput(
                    **{f: bound.arguments[f] for f in TRACE_INPUT_FIELDS}
                )

            self._tracer.start_trace(name, input_data)
            token = current_tracer.set(self._tracer)
            try:
                async for item in func(self, *args, **kwargs):
                    yield item
            finally:
                current_tracer.reset(token)
                self._tracer.end_trace()

        return wrapper

    return decorator


class LangfuseTracer:
    """Langfuse 追踪器 — 封装官方 SDK，提供简化的追踪接口。

    使用方式：
        tracer = LangfuseTracer()
        trace_id = tracer.start_trace("chat_with_citations", input_data)
        gen_id = tracer.start_generation(trace_id, "llm_call", ...)
        tracer.end_generation(gen_id, trace_id, output=..., usage=...)
        tracer.end_trace(trace_id, output=...)
    """

    def __init__(self) -> None:
        """延迟初始化 Langfuse SDK 客户端，失败时静默降级。

        从 src.config 读取 LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY /
        LANGFUSE_HOST。初始化失败仅记警告，不抛出异常。
        """
        self._client: Optional[Langfuse] = None
        try:
            from src.config import (
                LANGFUSE_HOST,
                LANGFUSE_PUBLIC_KEY,
                LANGFUSE_SECRET_KEY,
            )

            if not LANGFUSE_SECRET_KEY or not LANGFUSE_PUBLIC_KEY:
                logger.warning(
                    "LangfuseTracer: LANGFUSE_SECRET_KEY or LANGFUSE_PUBLIC_KEY not set"
                )
                return
            self._client = Langfuse(
                public_key=LANGFUSE_PUBLIC_KEY,
                secret_key=LANGFUSE_SECRET_KEY,
                host=LANGFUSE_HOST.rstrip("/"),
            )
            logger.info("LangfuseTracer initialized with official SDK")
        except Exception as e:
            logger.warning("LangfuseTracer init failed: %s", e)

    def _check_ready(self, method: str) -> bool:
        """检查客户端是否可用，不可用时记警告并返回 False。

        Args:
            method: 调用方方法名（用于日志标识）
        """
        if self._client is None:
            logger.warning("LangfuseTracer.{}: skipped (not initialized)", method)
            return False
        return True

    def start_trace(
        self,
        name: str,
        input_data: TraceInput | dict | None = None,
        session_id: Optional[str] = None,
    ) -> None:
        """创建新的 trace，trace ID 从 current_trace_id contextvar 自动读取。

        支持两种输入方式：
        1. 传入 TraceInput dataclass — 自动提取 session_id 和 input dict
        2. 传入 dict + session_id — 兼容旧调用方

        Args:
            name: trace 名称（如 "chat_with_citations"）
            input_data: trace 的输入数据（TraceInput 或 dict，可选）
            session_id: 关联的会话 ID（可选，TraceInput 传值时自动提取）
        """
        if self._client is None:
            logger.warning("LangfuseTracer.start_trace: skipped (not initialized)")
            return
        if isinstance(input_data, TraceInput):
            if session_id is None:
                session_id = input_data.session_id
            input_data = asdict(input_data)
        self._client.trace(
            name=name,
            input=input_data,
            session_id=session_id,
            id=current_trace_id.get(),
        )

    def end_trace(self, output: str | None = None) -> None:
        """更新 trace 的完成输出。

        trace_id 从 current_trace_id contextvar 自动读取（中间件兜底）。

        Args:
            output: trace 的输出数据（可选）
        """
        if self._client is None:
            logger.warning("LangfuseTracer.end_trace: skipped (not initialized)")
            return
        self._client.trace(id=current_trace_id.get(), output=output)

    def start_generation(
        self,
        name: str,
        input_data: list[dict] | None = None,
        model: Optional[str] = None,
        model_parameters: Optional[dict] = None,
    ) -> Optional[str]:
        """创建新的 generation（LLM 调用记录）并返回其 ID。

        trace_id 从 current_trace_id contextvar 自动读取（中间件兜底）。

        Args:
            name: generation 名称（如 "llm_stream"）
            input_data: LLM 调用的输入数据（可选）
            model: 使用的模型名称（可选）
            model_parameters: 模型参数（temperature 等，可选）

        Returns:
            generation ID，未初始化时返回 None
        """
        if self._client is None:
            logger.warning("LangfuseTracer.start_generation: skipped (not initialized)")
            return
        return self._client.generation(
            name=name,
            trace_id=current_trace_id.get(),
            input=input_data,
            model=model,
            model_parameters=model_parameters,
        ).id

    def end_generation(
        self,
        gen_id: str,
        output: str | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        """更新 generation 的输出和用量信息。

        trace_id 从 current_trace_id contextvar 自动读取（中间件兜底）。

        Args:
            gen_id: 要更新的 generation ID
            output: LLM 生成的输出文本（可选）
            usage: TokenUsage 用量统计（可选）
        """
        if self._client is None:
            logger.warning("LangfuseTracer.end_generation: skipped (not initialized)")
            return
        if not gen_id:
            logger.warning("LangfuseTracer.end_generation: skipped (no gen_id)")
            return
        sdk_usage: ModelUsage | None
        if usage:
            sdk_usage = cast(
                ModelUsage,
                {
                    "input": usage.prompt_tokens,
                    "output": usage.completion_tokens,
                    "total": usage.total_tokens,
                },
            )
        else:
            sdk_usage = None
        self._client.generation(
            id=gen_id,
            trace_id=current_trace_id.get(),
            output=output,
            usage=sdk_usage,
        )
