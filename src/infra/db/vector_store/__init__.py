"""向量存储模块 — 封装 ChromaDB 的增删查操作，返回类型化对象。

对外暴露的 VectorStore 类保持方法签名不变，仅返回类型从 list[dict] 改为
list[ChunkResult] / ChunkQueryResult。
"""

from typing import Optional
from src.config import CHROMA_COLLECTION_PREFIX
from src.infra.db.vector_store.client import ChromaClient
from src.infra.db.vector_store import store as _store
from src.infra.db.vector_store import search as _search
from src.infra.db.entities.search import ChunkResult, ChunkQueryResult
from src.parsers.base import ChunkData


class VectorStore:
    """ChromaDB 向量存储封装 — 每个知识库对应一个独立 collection。"""

    def __init__(self, persist_dir: Optional[str] = None):
        self._chroma = ChromaClient(persist_dir)

    @property
    def _embed_fn(self):
        return self._chroma._embed_fn

    def get_or_create_collection(self, kb_id: str):
        return self._chroma.get_or_create_collection(kb_id)

    def add_chunks(self, kb_id: str, chunks: list[ChunkData], doc_id: str) -> int:
        collection = self._chroma.get_or_create_collection(kb_id)
        return _store.add_chunks(collection, kb_id, chunks, doc_id)

    def similarity_search(
        self, kb_id: str, query: str, k: int = 5
    ) -> list[ChunkResult]:
        collection = self._chroma.get_or_create_collection(kb_id)
        return _search.similarity_search(collection, self._embed_fn, kb_id, query, k)

    def similarity_search_all(self, query: str, k: int = 10) -> list[ChunkResult]:
        names = self._chroma.list_collection_names()
        if not names:
            return []
        collections = {}
        for name in names:
            kb_id = name.removeprefix(CHROMA_COLLECTION_PREFIX)
            try:
                collections[kb_id] = self._chroma.get_or_create_collection(kb_id)
            except Exception:
                continue
        return _search.similarity_search_all(collections, self._embed_fn, query, k)

    def get_chunks_by_doc_id(self, doc_id: str, kb_id: str) -> list[ChunkResult]:
        collection = self._chroma.get_or_create_collection(kb_id)
        return _search.get_chunks_by_doc_id(collection, doc_id)

    def get_chunks_paginated(
        self, doc_id: str, kb_id: str, page: int = 1, page_size: int = 50
    ) -> ChunkQueryResult:
        collection = self._chroma.get_or_create_collection(kb_id)
        return _search.get_chunks_paginated(collection, doc_id, page, page_size)

    def delete_collection(self, kb_id: str) -> bool:
        name = self._chroma._collection_name(kb_id)
        return _store.delete_collection(
            self._chroma._get_client(), name, kb_id, self._chroma._collection_cache
        )

    def delete_document(self, kb_id: str, doc_id: str) -> int:
        collection = self._chroma.get_or_create_collection(kb_id)
        return _store.delete_document(collection, doc_id)

    def list_collections(self) -> list[str]:
        return self._chroma.list_collection_names()


__all__ = ["VectorStore", "ChunkResult", "ChunkQueryResult"]
