"""ChromaDB 连接管理和 collection 缓存。"""

from typing import Optional

import chromadb
from chromadb.api import ClientAPI
from chromadb.config import Settings
from loguru import logger

from src.config import CHROMA_COLLECTION_PREFIX, CHROMA_PERSIST_DIR, EMBEDDING_MODEL
from src.infra.db.vector_store.embedding import DashScopeEmbeddingFunction


class ChromaClient:
    """ChromaDB 连接管理 + collection 缓存（单例模式）。

    管理 PersistentClient 生命周期；对 collection 做内存缓存，避免重复创建；
    提供 collection 名称格式化、缓存清理和列表查询功能。
    """

    def __init__(self, persist_dir: Optional[str] = None):
        """初始化 ChromaDB 客户端管理器。

        Args:
            persist_dir: ChromaDB 持久化目录，None 时使用全局配置
        """
        self._persist_dir = persist_dir or CHROMA_PERSIST_DIR
        self._client: Optional[ClientAPI] = None
        self._collection_cache: dict[str, chromadb.Collection] = {}
        self._embed_fn = DashScopeEmbeddingFunction()

    def _get_client(self) -> ClientAPI:
        """获取或创建 PersistentClient 实例（惰性初始化）。

        Returns:
            ChromaDB PersistentClient 实例
        """
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
            logger.info(
                "ChromaDB PersistentClient created: persist_dir={} model={}",
                self._persist_dir,
                EMBEDDING_MODEL,
            )
        return self._client

    @staticmethod
    def _collection_name(kb_id: str) -> str:
        """将知识库 ID 转为 ChromaDB collection 完整名称。

        Args:
            kb_id: 知识库 ID

        Returns:
            添加了命名前缀的 collection 名称（移除了 - 字符）
        """
        clean_id = kb_id.replace("-", "")
        return f"{CHROMA_COLLECTION_PREFIX}{clean_id}"

    def get_or_create_collection(self, kb_id: str) -> chromadb.Collection:
        """获取或创建指定知识库的 collection（带缓存）。

        使用 HNSW 索引参数：cosine 距离、M=8、construction_ef=64。

        Args:
            kb_id: 知识库 ID

        Returns:
            ChromaDB Collection 实例
        """
        cache_key = kb_id
        if cache_key in self._collection_cache:
            return self._collection_cache[cache_key]
        name = self._collection_name(kb_id)
        client = self._get_client()
        collection = client.get_or_create_collection(
            name=name,
            embedding_function=self._embed_fn,
            metadata={
                "hnsw:space": "cosine",
                "hnsw:M": 8,
                "hnsw:construction_ef": 64,
            },
        )
        logger.debug("Got or created collection '{}' for kb_id={}", name, kb_id)
        self._collection_cache[cache_key] = collection
        return collection

    def delete_collection_cache(self, kb_id: str) -> None:
        """清除指定知识库的 collection 缓存。

        Args:
            kb_id: 知识库 ID
        """
        self._collection_cache.pop(kb_id, None)

    def list_collection_names(self) -> list[str]:
        """列出所有知识库 collection 名称。

        Returns:
            满足命名前缀的 collection 名称列表
        """
        client = self._get_client()
        names = client.list_collections()
        return [n.name for n in names if n.name.startswith(CHROMA_COLLECTION_PREFIX)]
