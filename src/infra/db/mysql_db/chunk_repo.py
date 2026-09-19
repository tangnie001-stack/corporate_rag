"""分块 Repo — chunks 表 CRUD 与 dense 检索。

本模块是 chunks 表的唯一 SQL 访问层：向量排序、jsonb 读写、按 doc/kb 的增删
都在这里，向量存储层（vector_store）只做编排与结果映射。
"""

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infra.db.models.chunk import ChunkModel
from src.infra.db.vector_store.mapping import ChunkRow, row_to_chunk_row


class ChunkRepo:
    """分块表的 SQL 访问层。"""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    async def upsert_chunks(self, rows: list[ChunkRow]) -> int:
        """按 (kb_id, doc_id, chunk_index) 幂等写入，冲突时整行覆盖。

        Args:
            rows: 待写入的分块行（形状见 mapping.ChunkRow）

        Returns:
            实际提交的行数
        """
        if not rows:
            return 0
        async with self._sf() as session:
            stmt = pg_insert(ChunkModel).values(
                [
                    {
                        "id": r.id,
                        "kb_id": r.kb_id,
                        "doc_id": r.doc_id,
                        "chunk_index": r.chunk_index,
                        "chunk_total": r.chunk_total,
                        "content": r.content,
                        "content_seg": r.content_seg,
                        "embedding": r.embedding,
                        "source": r.source,
                        "page": r.page,
                        "extra": r.extra,
                    }
                    for r in rows
                ]
            )
            # on_conflict 的 SET 与 excluded 按**列名**索引，jsonb 列名是 metadata；
            # ORM 属性名 extra 只用于 .values()。两者混用会生成 `extra = ...` 的非法 SQL。
            stmt = stmt.on_conflict_do_update(
                constraint="uq_chunks_kb_doc_idx",
                set_={
                    "content": stmt.excluded.content,
                    "content_seg": stmt.excluded.content_seg,
                    "embedding": stmt.excluded.embedding,
                    "source": stmt.excluded.source,
                    "page": stmt.excluded.page,
                    "metadata": stmt.excluded["metadata"],
                    "chunk_total": stmt.excluded.chunk_total,
                },
            )
            await session.execute(stmt)
            await session.commit()
        return len(rows)

    async def delete_tail(self, kb_id: str, doc_id: str, from_index: int) -> int:
        """删除某文档 chunk_index >= from_index 的尾部残留（重传后分块数变少时用）。"""
        async with self._sf() as session:
            result = await session.execute(
                delete(ChunkModel).where(
                    ChunkModel.kb_id == kb_id,
                    ChunkModel.doc_id == doc_id,
                    ChunkModel.chunk_index >= from_index,
                )
            )
            await session.commit()
            return result.rowcount or 0

    async def search_dense(
        self, kb_id: str, query_vec: list[float], k: int
    ) -> list[tuple[ChunkRow, float]]:
        """按余弦距离取 top-k（越小越相似）。"""
        async with self._sf() as session:
            distance = ChunkModel.embedding.cosine_distance(query_vec)
            stmt = (
                select(ChunkModel, distance.label("distance"))
                .where(ChunkModel.kb_id == kb_id, ChunkModel.embedding.is_not(None))
                .order_by(distance)
                .limit(k)
            )
            result = await session.execute(stmt)
            return [(row_to_chunk_row(m), float(dist)) for m, dist in result.all()]

    async def get_by_doc(self, doc_id: str, kb_id: str) -> list[ChunkRow]:
        """取某文档的全部分块，按 chunk_index 升序。"""
        async with self._sf() as session:
            stmt = (
                select(ChunkModel)
                .where(ChunkModel.kb_id == kb_id, ChunkModel.doc_id == doc_id)
                .order_by(ChunkModel.chunk_index)
            )
            result = await session.execute(stmt)
            return [row_to_chunk_row(m) for m in result.scalars().all()]

    async def get_paginated(
        self, doc_id: str, kb_id: str, page: int, page_size: int
    ) -> tuple[list[ChunkRow], int]:
        """分页取某文档的分块，返回 (当页行, 总数)。

        Args:
            doc_id: 文档 ID
            kb_id: 知识库 ID
            page: 页码，**1-based**（调用方保证 >= 1；<= 0 会产生负 OFFSET 并被 PG 拒绝）
            page_size: 每页条数
        """
        async with self._sf() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(ChunkModel)
                .where(ChunkModel.kb_id == kb_id, ChunkModel.doc_id == doc_id)
            )
            stmt = (
                select(ChunkModel)
                .where(ChunkModel.kb_id == kb_id, ChunkModel.doc_id == doc_id)
                .order_by(ChunkModel.chunk_index)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await session.execute(stmt)
            return [row_to_chunk_row(m) for m in result.scalars().all()], int(
                total or 0
            )

    async def get_by_kb(self, kb_id: str) -> list[ChunkRow]:
        """取整个知识库的全部分块（BM25 全量重建用）。"""
        async with self._sf() as session:
            stmt = (
                select(ChunkModel)
                .where(ChunkModel.kb_id == kb_id)
                .order_by(ChunkModel.doc_id, ChunkModel.chunk_index)
            )
            result = await session.execute(stmt)
            return [row_to_chunk_row(m) for m in result.scalars().all()]

    async def list_kb_ids(self) -> list[str]:
        """枚举有哪些知识库含分块（只读，不创建任何东西）。"""
        async with self._sf() as session:
            result = await session.execute(select(ChunkModel.kb_id).distinct())
            return [kb for kb in result.scalars().all()]

    async def delete_by_doc(self, kb_id: str, doc_id: str) -> int:
        """删除某文档的全部分块。"""
        async with self._sf() as session:
            result = await session.execute(
                delete(ChunkModel).where(
                    ChunkModel.kb_id == kb_id, ChunkModel.doc_id == doc_id
                )
            )
            await session.commit()
            return result.rowcount or 0

    async def delete_by_kb(self, kb_id: str) -> int:
        """删除某知识库的全部分块。"""
        async with self._sf() as session:
            result = await session.execute(
                delete(ChunkModel).where(ChunkModel.kb_id == kb_id)
            )
            await session.commit()
            return result.rowcount or 0
