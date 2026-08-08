"""模型工厂模块 — 提供 LLM、Embedding、Rerank 三类模型的实例化工厂函数。

所有模型参数从配置读取，支持通过 LiteLLM Proxy 或直接调用 Provider API。
切换 Provider 只需修改 .env 配置，无需改动代码。

核心组件：
  - with_retry：通用重试装饰器，支持指数退避
  - get_embeddings：创建文本向量化模型实例
  - get_llm：创建大语言模型实例
  - get_rerank：创建文本重排序模型实例
"""

import functools
import json
import time
from collections.abc import Callable
from typing import Any

from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from loguru import logger
from pydantic import SecretStr

from src.config import (
    CLASSIFIER_TEMPERATURE,
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_MODEL,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_KWARGS,
    LLM_MODEL,
    LLM_TEMPERATURE,
    RERANK_API_KEY,
    RERANK_MODEL,
    RETRY_BACKOFF_FACTOR,
    RETRY_INITIAL_INTERVAL,
    RETRY_MAX_ATTEMPTS,
    TOP_K_RERANK,
)


def with_retry(
    func: Callable | None = None,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
    initial_interval: float = RETRY_INITIAL_INTERVAL,
    backoff: float = RETRY_BACKOFF_FACTOR,
    retryable_exceptions: tuple = (Exception,),
) -> Callable:
    """通用重试装饰器 — 支持指数退避策略和精确异常类型匹配。

    用法灵活，可以不带参数或带参数使用：
        @with_retry
        def my_func(): ...

        @with_retry(max_attempts=5, initial_interval=2.0)
        def my_func(): ...

        @with_retry(retryable_exceptions=(TimeoutError, ConnectionError))
        def my_func(): ...

    Args:
        func: 被装饰的函数（由 Python 自动传入）
        max_attempts: 最大重试次数（默认 3 次）
        initial_interval: 首次重试等待时间（秒）
        backoff: 退避因子（每次等待时间乘以此值，如 2.0 表示翻倍）
        retryable_exceptions: 可重试的异常类型元组（默认所有 Exception）

    Returns:
        包装后的函数，失败时自动重试，超过次数后抛出最后一次的异常
    """
    # 当使用 @with_retry(max_attempts=5) 带参形式时，func 为 None
    # 返回一个 lambda，让 Python 再次调用 with_retry 并传入真正的 func
    if func is None:
        return lambda f: with_retry(
            f, max_attempts, initial_interval, backoff, retryable_exceptions
        )

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                return func(*args, **kwargs)
            except retryable_exceptions as e:
                last_error = e
                if attempt < max_attempts:
                    # 指数退避：wait = initial * backoff^(attempt-1)
                    # 第 1 次重试等 1s，第 2 次等 2s，第 3 次等 4s...
                    wait = initial_interval * (backoff ** (attempt - 1))
                    logger.warning(
                        "{} failed (attempt {}/{}): {}. Retrying in {:.1f}s...",
                        func.__name__,
                        attempt,
                        max_attempts,
                        e,
                        wait,
                    )
                    time.sleep(wait)
        # 所有重试均失败，记录错误并抛出最后一次异常
        logger.exception(
            "{} failed after {} attempts: {}", func.__name__, max_attempts, last_error
        )
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{func.__name__} failed after {max_attempts} attempts")

    return wrapper


def get_embeddings(model: str = EMBEDDING_MODEL) -> OpenAIEmbeddings:
    """创建文本向量化模型实例（配置驱动，通过 Proxy 或直连）。

    从环境变量读取 API Key 和 Base URL，支持 DashScope / DeepSeek / LiteLLM Proxy。

    Args:
        model: 模型名称，默认 qwen3.7-text-embedding

    Returns:
        OpenAIEmbeddings 实例
    """
    return OpenAIEmbeddings(
        model=model,
        api_key=SecretStr(EMBEDDING_API_KEY),
        base_url=EMBEDDING_BASE_URL,
        check_embedding_ctx_length=False,
        chunk_size=EMBEDDING_BATCH_SIZE,
    )


def get_llm(
    model: str = LLM_MODEL,
    temperature: float = LLM_TEMPERATURE,
    **kwargs: Any,
) -> ChatOpenAI:
    """创建大语言模型实例（配置驱动，通过 Proxy 或直连）。

    从环境变量读取 API Key、Base URL 和额外参数，支持 DashScope / DeepSeek / LiteLLM Proxy。

    Args:
        model: 模型名称，默认 qwen3.7-max
        temperature: 温度参数，越低越确定性（金融场景推荐 0.1）
        **kwargs: 额外参数，会与 LLM_KWARGS（JSON 环境变量）合并

    Returns:
        ChatOpenAI 实例
    """
    # 合并 LLM_KWARGS 和显式传入的 kwargs
    extra_kwargs: dict = json.loads(LLM_KWARGS)
    extra_kwargs.update(kwargs)
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=SecretStr(LLM_API_KEY),
        base_url=LLM_BASE_URL,
        callbacks=_content_logging_callbacks(),
        **extra_kwargs,
    )


def _content_logging_callbacks() -> list | None:
    """按 LLM_LOG_CONTENT 开关返回内容记录 callback（默认 None）。

    Returns:
        LLM_LOG_CONTENT 开启时返回 [LlmContentLoggingHandler]，否则 None。
    """
    from src.config import LLM_LOG_CONTENT

    if not LLM_LOG_CONTENT:
        return None
    from src.infra.llm.llm_content_logging import LlmContentLoggingHandler

    return [LlmContentLoggingHandler()]


def get_classify_llm() -> ChatOpenAI:
    """创建分类/路由专用 LLM 实例（小模型，关闭思考模式）。

    KBRouter 和 classify 节点使用，与 generate 节点的大模型分离，
    避免大模型在简单分类任务上浪费推理时间。

    Returns:
        关闭思考模式的 ChatOpenAI 实例
    """
    from src.config import CLASSIFY_MODEL

    extra_kwargs: dict = json.loads(LLM_KWARGS)
    extra_kwargs.update({"extra_body": {"enable_thinking": False}})
    return ChatOpenAI(
        model=CLASSIFY_MODEL,
        temperature=CLASSIFIER_TEMPERATURE,
        api_key=SecretStr(LLM_API_KEY),
        base_url=LLM_BASE_URL,
        callbacks=_content_logging_callbacks(),
        **extra_kwargs,
    )


def get_rerank(model: str = RERANK_MODEL, top_n: int = TOP_K_RERANK) -> DashScopeRerank:
    """创建文本重排序模型实例（固定走 DashScope Rerank API）。

    Rerank 不走 LiteLLM Proxy，因为 LiteLLM 不支持 DashScope 的 rerank 端点。
    API Key 使用 RERANK_API_KEY（默认 fallback 到 DASHSCOPE_API_KEY）。
    显式传入 client 以阻止 DashScopeRerank 的 model_validator 将 model 覆盖为 SDK 默认值。

    Args:
        model: 模型名称，默认 qwen3-rerank
        top_n: 重排序后保留的文档数量（默认 5）

    Returns:
        DashScopeRerank 实例
    """
    import dashscope

    return DashScopeRerank(
        model=model,
        top_n=top_n,
        api_key=RERANK_API_KEY,
        client=dashscope.TextReRank,
    )
