"""dense 检索的 PostgreSQL + pgvector 后端。

与 Chroma 后端（client.py / store.py / search.py）的关系：二者接口相同，
P2 期间并存；等价性验收通过后由 Task 9 切换装配并删除 Chroma 实现。

契约要点：
- distance 是**余弦距离**（pgvector `<=>`），消费方用 score = 1 - distance；
- k 的上限仍是 100 —— 该上限源自 Chroma（vector_store/search.py:43），
  PG 无此限制，这里保留它是为了不把「能力提升」混进迁移等价性验收。
"""

from loguru import logger

from src.chunking.validator import ChunkData
from src.core import logging as core_logging
from src.core.log_events import Event
from src.core.logging import LOG_MAX_BODY
from src.infra.db.mysql_db.chunk_repo import ChunkRepo
from src.infra.db.vector_store.mapping import build_rows
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
            embeddings = self._embed_fn.embed_documents([c.content for c in chunks])
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
