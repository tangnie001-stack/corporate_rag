"""向量存储模块 — 封装 ChromaDB 的增删查操作，返回类型化对象。

对外暴露的 VectorStore 类保持方法签名不变，仅返回类型从 list[dict] 改为
list[ChunkResult] / ChunkQueryResult。
"""

from src.chunking.validator import ChunkData
from src.config import CHROMA_COLLECTION_PREFIX
from src.infra.db.vector_store import search as _search
from src.infra.db.vector_store import store as _store
from src.infra.db.vector_store.client import ChromaClient
from src.infra.db.vector_store.types import ChunkQueryResult, ChunkResult


class VectorStore:
    """ChromaDB 向量存储封装 — 每个知识库对应一个独立 collection。

    对外提供增删查的统一接口，内部委托给 store / search / client 子模块。
    """

    def __init__(self, persist_dir: str | None = None):
        """初始化向量存储实例。

        Args:
            persist_dir: ChromaDB 持久化目录，None 时使用全局配置路径
        """
        self._chroma = ChromaClient(persist_dir)

    @property
    def _embed_fn(self):
        """获取嵌入函数实例（属性委托给 ChromaClient）。"""
        return self._chroma._embed_fn

    def get_or_create_collection(self, kb_id: str):
        """获取或创建指定知识库的 collection（属性委托给 ChromaClient）。

        Args:
            kb_id: 知识库 ID

        Returns:
            ChromaDB Collection 实例
        """
        return self._chroma.get_or_create_collection(kb_id)

    def add_chunks(
        self,
        kb_id: str,
        chunks: list[ChunkData],
        doc_id: str,
        embeddings: list[list[float]] | None = None,
    ) -> int:
        """批量写入分块到指定知识库的 collection。

        Args:
            kb_id: 知识库 ID
            chunks: 分块数据列表
            doc_id: 文档 ID
            embeddings: 可选的预计算 embedding，传入后跳过 embedding 模型调用

        Returns:
            实际写入的分块数量
        """
        with self._chroma._lock:
            collection = self._chroma.get_or_create_collection(kb_id)
            return _store.add_chunks(collection, kb_id, chunks, doc_id, embeddings)

    def similarity_search(
        self, kb_id: str, query: str, k: int = 5
    ) -> list[ChunkResult]:
        """在单个知识库中执行语义检索。

        Args:
            kb_id: 知识库 ID
            query: 查询文本
            k: 返回结果数量上限，默认 5

        Returns:
            检索结果列表，按相关性降序排列
        """
        with self._chroma._lock:
            collection = self._chroma.get_or_create_collection(kb_id)
            return _search.similarity_search(
                collection, self._embed_fn, kb_id, query, k
            )

    def similarity_search_all(self, query: str, k: int = 10) -> list[ChunkResult]:
        """在所有知识库中执行语义检索，合并后排序取 top-k。

        Args:
            query: 查询文本
            k: 返回结果数量上限，默认 10

        Returns:
            合并排序后的检索结果列表
        """
        with self._chroma._lock:
            names = self._chroma.list_collection_names()
            if not names:
                return []
            collections = {}
            for name in names:
                kb_id = name.removeprefix(CHROMA_COLLECTION_PREFIX)
                try:
                    collections[kb_id] = self._chroma.get_or_create_collection(kb_id)
                except Exception:  # noqa: BLE001, S112
                    continue
            return _search.similarity_search_all(collections, self._embed_fn, query, k)

    def similarity_search_multi(
        self, kb_ids: list[str], query: str, k: int = 5
    ) -> list[ChunkResult]:
        """在指定多个知识库中执行语义检索，合并后排序取 top-k。

        每个 KB 单独搜索 TOP_K_RETRIEVAL 条，合并后按距离升序取 top-k。
        chromadb PersistentClient 非线程安全，检索在 ChromaClient 锁内串行执行。

        Args:
            kb_ids: 知识库 ID 列表
            query: 查询文本
            k: 返回结果数量上限，默认 5

        Returns:
            合并排序后的检索结果列表
        """
        all_results: list[ChunkResult] = []
        for kb_id in kb_ids:
            all_results.extend(self.similarity_search(kb_id, query, k) or [])

        all_results.sort(
            key=lambda r: r.distance if r.distance is not None else float("inf")
        )
        return all_results[:k]

    def get_chunks_by_doc_id(self, doc_id: str, kb_id: str) -> list[ChunkResult]:
        """查询指定文档的所有分块。

        Args:
            doc_id: 文档 ID
            kb_id: 知识库 ID

        Returns:
            分块结果列表
        """
        with self._chroma._lock:
            collection = self._chroma.get_or_create_collection(kb_id)
            return _search.get_chunks_by_doc_id(collection, doc_id)

    def get_chunks_paginated(
        self, doc_id: str, kb_id: str, page: int = 1, page_size: int = 50
    ) -> ChunkQueryResult:
        """分页查询指定文档的分块。

        Args:
            doc_id: 文档 ID
            kb_id: 知识库 ID
            page: 页码，从 1 开始，默认 1
            page_size: 每页数量，默认 50

        Returns:
            分页查询结果（含 items / total / page / page_size）
        """
        collection = self._chroma.get_or_create_collection(kb_id)
        return _search.get_chunks_paginated(collection, doc_id, page, page_size)

    def delete_collection(self, kb_id: str) -> bool:
        """删除整个知识库的 collection。

        Args:
            kb_id: 知识库 ID

        Returns:
            是否删除成功
        """
        name = self._chroma._collection_name(kb_id)
        return _store.delete_collection(
            self._chroma._get_client(), name, kb_id, self._chroma._collection_cache
        )

    def delete_document(self, kb_id: str, doc_id: str) -> int:
        """删除指定文档的所有分块。

        Args:
            kb_id: 知识库 ID
            doc_id: 文档 ID

        Returns:
            删除的分块数量
        """
        collection = self._chroma.get_or_create_collection(kb_id)
        return _store.delete_document(collection, doc_id)

    def list_collections(self) -> list[str]:
        """列出所有知识库 collection 名称。

        Returns:
            满足命名前缀的 collection 名称列表
        """
        return self._chroma.list_collection_names()


__all__ = ["ChunkQueryResult", "ChunkResult", "VectorStore"]
