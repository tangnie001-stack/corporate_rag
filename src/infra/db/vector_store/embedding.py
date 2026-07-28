"""Embedding 适配器 — 为 ChromaDB 提供统一嵌入函数接口。

使用配置驱动的 OpenAIEmbeddings 实例，支持通过 LiteLLM Proxy 或直连。
"""

from chromadb.api.types import Documents, Embeddings, EmbeddingFunction
from langchain_openai import OpenAIEmbeddings
from src.config import EMBEDDING_MODEL, EMBEDDING_API_KEY, EMBEDDING_BASE_URL


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
            api_key=api_key or EMBEDDING_API_KEY,
            base_url=base_url or EMBEDDING_BASE_URL,
        )

    def __call__(self, input: Documents) -> Embeddings:
        """将文档列表转为嵌入向量（ChromaDB 内部调用）。

        Args:
            input: 待编码的文档字符串列表

        Returns:
            嵌入向量列表
        """
        return self._embedding.embed_documents(list(input))

    def embed_query(self, text: str) -> list[float]:
        """将单条查询文本转为嵌入向量。

        Args:
            text: 查询文本

        Returns:
            查询文本的嵌入向量
        """
        return self._embedding.embed_query(text)
