"""DashScope Embedding 适配器。"""

from chromadb.api.types import Documents, Embeddings, EmbeddingFunction
from src.models import FixedDimDashScopeEmbeddings
from src.config import EMBEDDING_MODEL, DASHSCOPE_API_KEY


class DashScopeEmbeddingFunction(EmbeddingFunction):
    """DashScope 云端 Embedding 适配器，符合 ChromaDB 0.5+ 接口规范。

    包装 FixedDimDashScopeEmbeddings，适配 ChromaDB 的 EmbeddingFunction 协议。
    """

    def __init__(self, model: str = EMBEDDING_MODEL, api_key: str = DASHSCOPE_API_KEY):
        """初始化 Embedding 适配器。

        Args:
            model: DashScope Embedding 模型名称，默认使用全局配置
            api_key: DashScope API Key，默认使用全局配置
        """
        self._embedding = FixedDimDashScopeEmbeddings(
            model=model, dashscope_api_key=api_key
        )

    def __call__(self, input: Documents) -> Embeddings:
        """将文档列表转为嵌入向量（ChromaDB 内部调用）。

        Args:
            input: 待编码的文档字符串列表

        Returns:
            嵌入向量列表，每个向量维度固定
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
