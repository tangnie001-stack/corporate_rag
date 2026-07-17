"""模型工厂模块 — 提供 LLM、Embedding、Rerank 三类模型的实例化工厂函数。

本模块封装了 DashScope API 的三种模型创建逻辑，并提供了通用的指数退避重试装饰器。
所有模型实例采用延迟初始化（调用时才创建），避免模块导入时产生不必要的网络请求。

核心组件：
  - with_retry：通用重试装饰器，支持指数退避
  - get_embeddings：创建文本向量化模型实例
  - get_llm：创建大语言模型实例（OpenAI 兼容接口）
  - get_rerank：创建文本重排序模型实例
"""

import time
import functools
from typing import Callable, TypeVar

from loguru import logger
from langchain_openai import ChatOpenAI
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank

from src.config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    EMBEDDING_DIMENSION,
    LLM_MODEL,
    EMBEDDING_MODEL,
    RERANK_MODEL,
    LLM_TEMPERATURE,
    TOP_K_RERANK,
    RETRY_MAX_ATTEMPTS,
    RETRY_INITIAL_INTERVAL,
    RETRY_BACKOFF_FACTOR,
)

# 泛型类型变量，用于装饰器保留原函数的类型签名
F = TypeVar("F", bound=Callable)


def with_retry(
    func: F = None,
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
        raise last_error

    return wrapper


class FixedDimDashScopeEmbeddings(DashScopeEmbeddings):
    """始终以固定维度调用 DashScope Embedding API。

    确保无论使用哪个模型版本（text-embedding-v3/v4），输出的向量维度一致，
    切换模型时无需重建 ChromaDB collection。
    """

    EMBEDDING_DIMENSION: int = EMBEDDING_DIMENSION

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """将文本列表转为固定维度的向量。

        分批调用 DashScope API：单次 batch 上限为 20 条，
        超出时自动按 20 条分批后再合并结果。

        Args:
            texts: 待编码的文本列表

        Returns:
            固定维度的向量列表
        """
        from langchain_community.embeddings.dashscope import embed_with_retry

        EMBEDDING_BATCH_SIZE = 20
        all_embeddings: list[list[float]] = []

        logger.debug(
            "Embedding documents: model={} batch_size={} dim={}",
            self.model,
            len(texts),
            self.EMBEDDING_DIMENSION,
        )

        for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[i : i + EMBEDDING_BATCH_SIZE]
            result = embed_with_retry(
                self,
                input=batch,
                text_type="document",
                model=self.model,
                dimensions=self.EMBEDDING_DIMENSION,
            )
            all_embeddings.extend(item["embedding"] for item in result)

        return all_embeddings

    def embed_query(self, text: str) -> list[float]:
        """将单条文本转为固定维度的向量。

        Args:
            text: 待编码的文本

        Returns:
            固定维度的向量
        """
        return self.embed_documents([text])[0]


def get_embeddings(model: str = EMBEDDING_MODEL) -> FixedDimDashScopeEmbeddings:
    """创建固定维度的 DashScope 文本向量化模型实例。

    始终输出 1024 维向量，切换 embedding 模型时无需重建 ChromaDB collection。

    Args:
        model: 模型名称，默认 qwen3.7-text-embedding

    Returns:
        FixedDimDashScopeEmbeddings 实例，可直接用于 LangChain 的 embedding 接口
    """
    return FixedDimDashScopeEmbeddings(
        model=model,
        dashscope_api_key=DASHSCOPE_API_KEY,
    )


def get_llm(model: str = LLM_MODEL, temperature: float = LLM_TEMPERATURE) -> ChatOpenAI:
    """创建 DashScope 大语言模型实例（使用 OpenAI 兼容接口）。

    用于根据检索到的文档上下文生成最终回答。DashScope 的 qwen 系列模型
    兼容 OpenAI API 格式，因此使用 ChatOpenAI 作为客户端。

    Args:
        model: 模型名称，默认 qwen-max
        temperature: 温度参数，越低越确定性（金融场景推荐 0.1）

    Returns:
        ChatOpenAI 实例，支持 .stream() 流式输出和 .invoke() 同步调用
    """
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
    )


def get_rerank(model: str = RERANK_MODEL, top_n: int = TOP_K_RERANK) -> DashScopeRerank:
    """创建 DashScope 文本重排序模型实例。

    对向量检索返回的候选文档进行二次精排，按相关性重新打分，
    确保最终送入 LLM 的上下文是最相关的片段。

    Args:
        model: 模型名称，默认 gte-rerank-v2
        top_n: 重排序后保留的文档数量（默认 5）

    Returns:
        DashScopeRerank 实例，调用 .rerank(query, docs) 返回排序后的结果
    """
    return DashScopeRerank(
        model=model,
        top_n=top_n,
        dashscope_api_key=DASHSCOPE_API_KEY,
    )
