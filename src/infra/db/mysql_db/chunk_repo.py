"""分块 Repo — chunks 表 CRUD、dense 与词法取数。

本模块是 chunks 表的唯一 SQL 访问层：向量排序、词法匹配、jsonb 读写、按 doc/kb
的增删都在这里，向量存储层（vector_store）只做编排与结果映射。
"""

from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.lexical_query import LexicalQuery, escape_like
from src.infra.db.models.chunk import ChunkModel
from src.infra.db.transaction import session_scope
from src.infra.db.vector_store.mapping import ChunkRow, row_to_chunk_row


class ChunkRepo:
    """分块表的 SQL 访问层。"""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    async def upsert_chunks(
        self, rows: list[ChunkRow], session: AsyncSession | None = None
    ) -> int:
        """按 (kb_id, doc_id, chunk_index) 幂等写入，冲突时整行覆盖。

        隐含前提：同一 `doc_id` 不得跨 `kb_id` 出现 —— 主键 `id = {doc_id}:{chunk_index}`
        由本层之外生成，而 `ON CONFLICT` 只面向唯一约束 `uq_chunks_kb_doc_idx`、
        不覆盖 PK `id`；跨 kb 复用同一 `doc_id` 会撞 PK 抛 `IntegrityError`。

        Args:
            rows: 待写入的分块行（形状见 mapping.ChunkRow）
            session: 外部事务边界提供的会话；None 时本方法自开会话并提交

        Returns:
            实际提交的行数
        """
        if not rows:
            return 0
        async with session_scope(self._sf, session) as session:  # noqa: PLR1704  # 复用形参名 session 是刻意的：先取形参再绑定会话
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
        return len(rows)

    async def delete_tail(
        self,
        kb_id: str,
        doc_id: str,
        from_index: int,
        session: AsyncSession | None = None,
    ) -> int:
        """删除某文档 chunk_index >= from_index 的尾部残留（重传后分块数变少时用）。

        Args:
            kb_id: 知识库 ID
            doc_id: 文档 ID
            from_index: 起始分块序号（含）
            session: 外部事务边界提供的会话；None 时本方法自开会话并提交

        Returns:
            实际删除的行数
        """
        async with session_scope(self._sf, session) as session:  # noqa: PLR1704  # 复用形参名 session 是刻意的：先取形参再绑定会话
            result = await session.execute(
                delete(ChunkModel).where(
                    ChunkModel.kb_id == kb_id,
                    ChunkModel.doc_id == doc_id,
                    ChunkModel.chunk_index >= from_index,
                )
            )
            return cast(CursorResult[Any], result).rowcount or 0

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

    async def search_lexical(
        self, kb_id: str, plan: LexicalQuery, k: int
    ) -> list[tuple[ChunkRow, float]]:
        """按词法相关性取 top-k。

        词元非空时走 `tsv @@ to_tsquery(...)` 并按 `ts_rank` 降序；词元全被滤掉时
        （见 lexical_query 的 H1）降级为正文子串匹配，此时得分恒为 0.0。
        两种路径都以 `id` / `(doc_id, chunk_index)` 作 tiebreaker，保证同查询可复现
        （融合是纯 Python 排序，上游结果顺序不定会让 RRF 输出漂移）。

        Args:
            kb_id: 知识库 ID
            plan: 查询条件（由调用方构造，见 lexical_query.build_lexical_query）
            k: 返回条数上限

        Returns:
            (行, 词法得分) 列表；tsquery 路径按得分降序，子串兜底路径按文档与序号升序
        """
        # 空/纯空白原文没有任何可检内容，直接短路：既不开数据库会话，也不得提交
        # tsquery 或退化为子串匹配（`LIKE '%%'` 会命中全库）
        if plan.is_blank:
            return []
        async with self._sf() as session:
            if plan.use_substring:
                stmt = (
                    select(ChunkModel, literal(0.0).label("lexical_score"))
                    .where(
                        ChunkModel.kb_id == kb_id,
                        ChunkModel.content.like(
                            f"%{escape_like(plan.raw)}%", escape="\\"
                        ),
                    )
                    .order_by(ChunkModel.doc_id, ChunkModel.chunk_index)
                    .limit(k)
                )
            else:
                tsquery = func.to_tsquery("simple", plan.tsquery)
                lexical_score = func.ts_rank(ChunkModel.tsv, tsquery).label(
                    "lexical_score"
                )
                stmt = (
                    select(ChunkModel, lexical_score)
                    .where(
                        ChunkModel.kb_id == kb_id,
                        ChunkModel.tsv.op("@@")(tsquery),
                    )
                    .order_by(lexical_score.desc(), ChunkModel.id)
                    .limit(k)
                )
            result = await session.execute(stmt)
            return [(row_to_chunk_row(m), float(score)) for m, score in result.all()]

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
        """取整个知识库的全部分块（空库检查 / 全量读取用）。"""
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

    async def delete_by_doc(
        self, kb_id: str, doc_id: str, session: AsyncSession | None = None
    ) -> int:
        """删除某文档的全部分块。

        Args:
            kb_id: 知识库 ID
            doc_id: 文档 ID
            session: 外部事务边界提供的会话；None 时本方法自开会话并提交

        Returns:
            实际删除的行数
        """
        async with session_scope(self._sf, session) as session:  # noqa: PLR1704  # 复用形参名 session 是刻意的：先取形参再绑定会话
            result = await session.execute(
                delete(ChunkModel).where(
                    ChunkModel.kb_id == kb_id, ChunkModel.doc_id == doc_id
                )
            )
            return cast(CursorResult[Any], result).rowcount or 0

    async def delete_by_kb(
        self, kb_id: str, session: AsyncSession | None = None
    ) -> int:
        """删除某知识库的全部分块。

        Args:
            kb_id: 知识库 ID
            session: 外部事务边界提供的会话；None 时本方法自开会话并提交

        Returns:
            实际删除的行数
        """
        async with session_scope(self._sf, session) as session:  # noqa: PLR1704  # 复用形参名 session 是刻意的：先取形参再绑定会话
            result = await session.execute(
                delete(ChunkModel).where(ChunkModel.kb_id == kb_id)
            )
            return cast(CursorResult[Any], result).rowcount or 0
