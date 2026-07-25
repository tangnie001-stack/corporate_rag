"""DashScope Embedding 适配器。"""

from chromadb.api.types import Documents, Embeddings, EmbeddingFunction
from src.models import FixedDimDashScopeEmbeddings
from src.config import EMBEDDING_MODEL, DASHSCOPE_API_KEY


class DashScopeEmbeddingFunction(EmbeddingFunction):
    """DashScope 云端 Embedding 适配器，符合 ChromaDB 0.5+ 接口规范。"""

    def __init__(self, model: str = EMBEDDING_MODEL, api_key: str = DASHSCOPE_API_KEY):
        self._embedding = FixedDimDashScopeEmbeddings(
            model=model, dashscope_api_key=api_key
        )

    def __call__(self, input: Documents) -> Embeddings:
        return self._embedding.embed_documents(list(input))

    def embed_query(self, text: str) -> list[float]:
        return self._embedding.embed_query(text)
