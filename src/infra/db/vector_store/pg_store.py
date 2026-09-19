"""dense 检索的 PostgreSQL + pgvector 后端。

与 Chroma 后端（client.py / store.py / search.py）的关系：二者接口相同，
P2 期间并存；等价性验收通过后由 Task 9 切换装配并删除 Chroma 实现。

契约要点：
- distance 是**余弦距离**（pgvector `<=>`），消费方用 score = 1 - distance；
- k 的上限仍是 100 —— 该上限源自 Chroma（vector_store/search.py:43），
  PG 无此限制，这里保留它是为了不把「能力提升」混进迁移等价性验收。
"""

import asyncio

from loguru import logger

from src.chunking.validator import ChunkData
from src.config import EMBEDDING_MODEL
from src.core import logging as core_logging
from src.core.log_events import Event
from src.core.logging import LOG_MAX_BODY
from src.infra.db.mysql_db.chunk_repo import ChunkRepo
from src.infra.db.vector_store.mapping import build_rows, row_to_chunk_result
from src.infra.db.vector_store.types import ChunkQueryResult, ChunkResult
from src.models import get_embeddings

# Chroma 后端沿用的硬上限（vector_store/search.py:43 的 n_results=min(k, 100)）。
# PG 本身没有这个限制；保留它是为了让等价性验收只度量「存储替换」，
# 不把检索条数的能力变化混进来（Task 6 dense_search 与 Task 8 会 import 它）。
MAX_QUERY_K = 100


class QueryEmbedder:
    """查询/文档的向量化入口 —— 薄封装，便于测试注入假实现。"""

    def embed_query(self, text: str) -> list[float]:
        """把查询文本向量化。

        Args:
            text: 查询文本

        Returns:
            1024 维向量
        """
        return get_embeddings().embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化文档文本。

        Args:
            texts: 文本列表

        Returns:
            与输入一一对应的向量列表
        """
        return get_embeddings().embed_documents(texts)


class PgVectorStore:
    """分块向量存储的 PostgreSQL 后端（单表 + kb_id 列，无 collection 概念）。"""

    def __init__(
        self,
        chunk_repo: ChunkRepo | None = None,
        embed_fn: QueryEmbedder | None = None,
    ) -> None:
        """初始化。

        Args:
            chunk_repo: chunks 表访问层；缺省时用应用默认 session_factory 构造
            embed_fn: 向量化入口；缺省时用 QueryEmbedder（DashScope）
        """
        if chunk_repo is None:
            from src.infra.db.engine import session_factory

            chunk_repo = ChunkRepo(session_factory)
        if embed_fn is None:
            embed_fn = QueryEmbedder()
        self._repo = chunk_repo
        self._embed_fn = embed_fn

    async def add_chunks(
        self,
        kb_id: str,
        chunks: list[ChunkData],
        doc_id: str,
        embeddings: list[list[float]] | None = None,
    ) -> int:
        """批量写入分块（按 (kb_id, doc_id, chunk_index) 幂等覆盖）。

        Args:
            kb_id: 知识库 ID
            chunks: 分块数据列表
            doc_id: 文档 ID
            embeddings: 预计算向量；为 None 时在此处补算（document_service 恒预计算，
                因此正常路径不会走到这里；保留它是为了不改变既有方法契约）

        Returns:
            实际写入的分块数量
        """
        if not chunks:
            return 0
        if embeddings is None:
            # 向量化是同步的 HTTP 调用 → 必须 offload，否则阻塞事件循环（单 worker 下会冻住所有请求与 SSE）
            embeddings = await asyncio.to_thread(
                self._embed_fn.embed_documents, [c.content for c in chunks]
            )
        rows = build_rows(kb_id, doc_id, chunks, embeddings)
        await self._repo.upsert_chunks(rows)
        # 分块数变少时删掉尾部残留（upsert 只覆盖 [0, len(rows)) 区间）
        await self._repo.delete_tail(kb_id, doc_id, len(rows))
        core_logging.log_event(
            Event.CHUNKS_ADDED, kb_id=kb_id, doc_id=doc_id, count=len(rows)
        )
        data_str = str(rows)[:LOG_MAX_BODY]
        logger.debug(
            "[PG] method=add_chunks | kb_id={} | doc_id={} | rows={} | data={}",
            kb_id,
            doc_id,
            len(rows),
            data_str,
        )
        return len(rows)

    async def dense_search(
        self, kb_id: str, query: str, k: int = 5
    ) -> list[ChunkResult]:
        """dense 路取 top-k（余弦距离升序），并填充 dense_rank。

        Args:
            kb_id: 知识库 ID
            query: 查询文本
            k: 返回条数上限（内部再按 MAX_QUERY_K 截断）

        Returns:
            按余弦距离升序的 ChunkResult；每项 distance 有值、dense_rank 为 0 起的名次
        """
        effective_k = min(k, MAX_QUERY_K)
        query_vec = await asyncio.to_thread(self._embed_fn.embed_query, query)
        pairs = await self._repo.search_dense(kb_id, query_vec, effective_k)
        results = [
            row_to_chunk_result(row, distance=distance, dense_rank=rank)
            for rank, (row, distance) in enumerate(pairs)
        ]
        core_logging.log_event(
            Event.SEARCH_RESULT,
            kb_id=kb_id,
            query_len=len(query),
            result_count=len(results),
            model=EMBEDDING_MODEL,
        )
        logger.debug(
            "[PG] method=dense_search | kb_id={} | rows={} | data={}",
            kb_id,
            len(results),
            str(results)[:LOG_MAX_BODY],
        )
        return results

    async def similarity_search(
        self, kb_id: str, query: str, k: int = 5
    ) -> list[ChunkResult]:
        """dense 检索入口（dense_search 的别名，保留既有方法名与语义）。

        语义与 Chroma 后端一致：余弦**距离**，越小越相似；消费方用 score = 1 - distance。
        """
        return await self.dense_search(kb_id, query, k=k)

    async def get_chunks_by_doc_id(self, doc_id: str, kb_id: str) -> list[ChunkResult]:
        """取某文档的全部分块（分路字段为 None）。"""
        rows = await self._repo.get_by_doc(doc_id, kb_id)
        return [row_to_chunk_result(row) for row in rows]

    async def get_chunks_paginated(
        self, doc_id: str, kb_id: str, page: int = 1, page_size: int = 50
    ) -> ChunkQueryResult:
        """分页取某文档的分块。

        Args:
            doc_id: 文档 ID
            kb_id: 知识库 ID
            page: 页码，**1-based**（调用方保证 >= 1；<= 0 会被 PG 拒绝）
            page_size: 每页条数
        """
        rows, total = await self._repo.get_paginated(doc_id, kb_id, page, page_size)
        return ChunkQueryResult(
            items=[row_to_chunk_result(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_all_chunks(self, kb_id: str) -> list[ChunkResult]:
        """取整个知识库的全部分块（BM25 全量重建用）。"""
        rows = await self._repo.get_by_kb(kb_id)
        core_logging.log_event(Event.CHUNKS_READ, kb_id=kb_id, count=len(rows))
        return [row_to_chunk_result(row) for row in rows]

    async def list_collections(self) -> list[str]:
        """枚举含分块的知识库 ID（PG 无 collection，语义是「有哪些 kb 有分块」）。

        只读，不创建任何东西。
        """
        return await self._repo.list_kb_ids()

    async def delete_document(self, kb_id: str, doc_id: str) -> int:
        """删除某文档的全部分块，返回删除行数。"""
        return await self._repo.delete_by_doc(kb_id, doc_id)

    async def delete_collection(self, kb_id: str) -> bool:
        """删除某知识库的全部分块；返回是否删除了行。"""
        deleted = await self._repo.delete_by_kb(kb_id)
        return deleted > 0

    async def get_or_create_collection(self, kb_id: str) -> str:
        """PG 无 collection 概念：该方法不产生副作用，直接返回 kb_id。

        保留是为了维持既有方法名契约；调用方不应依赖它「创建」任何东西。
        """
        return kb_id
