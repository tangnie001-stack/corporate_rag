"""Embedding 适配器 — 为 ChromaDB 提供统一嵌入函数接口。

使用配置驱动的 OpenAIEmbeddings 实例，支持通过 LiteLLM Proxy 或直连。
"""

from typing import cast

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from langchain_openai import OpenAIEmbeddings
from loguru import logger
from pydantic import SecretStr

from src.config import (
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_MODEL,
)


class DashScopeEmbeddingFunction(EmbeddingFunction):
    """统一 Embedding 适配器，符合 ChromaDB 0.5+ 接口规范。

    包装 OpenAIEmbeddings（配置驱动），适配 ChromaDB 的 EmbeddingFunction 协议。
    可通过环境变量切换实际 Provider。
    """

    def __init__(
        self,
        model: str = EMBEDDING_MODEL,
        api_key: str = "",
        base_url: str = "",
    ):
        """初始化 Embedding 适配器。

        Args:
            model: Embedding 模型名称，默认使用 EMBEDDING_MODEL
            api_key: API Key，留空使用 EMBEDDING_API_KEY
            base_url: API 地址，留空使用 EMBEDDING_BASE_URL
        """
        self._embedding = OpenAIEmbeddings(
            model=model,
            api_key=SecretStr(api_key or EMBEDDING_API_KEY),
            base_url=base_url or EMBEDDING_BASE_URL,
            check_embedding_ctx_length=False,
            chunk_size=EMBEDDING_BATCH_SIZE,
        )
        self._model = model

    def __call__(self, input: Documents) -> Embeddings:
        """将文档列表转为嵌入向量（ChromaDB 内部调用）。

        Args:
            input: 待编码的文档字符串列表

        Returns:
            嵌入向量列表
        """
        logger.debug(
            "Embedding call: model={} docs={}",
            self._model,
            len(input),
        )
        return cast(Embeddings, self._embedding.embed_documents(list(input)))

    def embed_query(self, text: str) -> list[float]:  # type: ignore[override]
        """将单条查询文本转为嵌入向量。

        注意：这是项目自定义扩展（search / kb_router 依赖 单文本→单向量），
        与 ChromaDB EmbeddingFunction 协议的 input: Documents → Embeddings
        签名不同，属于有意偏离，故标注 ignore[override]。

        Args:
            text: 查询文本

        Returns:
            查询文本的嵌入向量
        """
        logger.debug(
            "Embedding embed_query: model={} query_len={}",
            self._model,
            len(text),
        )
        return self._embedding.embed_query(text)
