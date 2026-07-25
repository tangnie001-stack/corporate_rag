"""文档 Repo — document 表 CRUD。"""

from src.config.queries import (
    INSERT_DOCUMENT,
    SELECT_DOCUMENTS_BY_KB_ID,
    SELECT_DOC_NAMES_BY_IDS,
    UPDATE_DOCUMENT_STATUS,
    SOFT_DELETE_DOCUMENT_BY_ID,
    SOFT_DELETE_DOCUMENTS_BY_KB_ID,
)
from src.infra.db.entities import DocEntity


class DocumentRepo:
    """文档 CRUD 仓库。

    封装 document 表的所有查询操作，返回 DocEntity 类型对象。
    """

    def __init__(self, mysql_db):
        """初始化 DocumentRepo。

        Args:
            mysql_db: MySQLDB 实例，用于获取连接池
        """
        self._pool_getter = mysql_db._get_pool

    async def add_document(self, doc: DocEntity) -> None:
        """插入一条文档记录。

        Args:
            doc: 待插入的文档实体
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    INSERT_DOCUMENT,
                    (
                        doc.id,
                        doc.kb_id,
                        doc.user_id,
                        doc.filename,
                        doc.file_type,
                        doc.file_size,
                        doc.status,
                        doc.file_path,
                        doc.hash,
                        doc.processing_state,
                        doc.processing_progress,
                        doc.processing_message,
                        doc.chunk_strategy,
                        doc.meta_info,
                    ),
                )
            await conn.commit()

    async def get_documents(self, kb_id: str) -> list[DocEntity]:
        """查询指定知识库的所有文档。

        Args:
            kb_id: 知识库 UUID

        Returns:
            文档实体列表
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_DOCUMENTS_BY_KB_ID, (kb_id,))
                rows = await cursor.fetchall()
        return [DocEntity(**row) for row in rows]

    async def get_doc_names(self, doc_ids: list[str]) -> dict[str, str]:
        """批量查询文档名称。

        Args:
            doc_ids: 文档 UUID 列表

        Returns:
            {文档 ID: 文档名称} 的字典
        """
        if not doc_ids:
            return {}
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                placeholders = ", ".join(["%s"] * len(doc_ids))
                sql = SELECT_DOC_NAMES_BY_IDS.format(placeholders)
                await cursor.execute(sql, doc_ids)
                rows = await cursor.fetchall()
        return {row["id"]: row["filename"] for row in rows}

    async def update_document_status(self, doc_id: str, **kwargs) -> None:
        """更新文档的处理状态和进度。

        Args:
            doc_id: 文档 UUID
            **kwargs: 可更新字段（status, chunk_count, error_msg, processing_state,
                      processing_progress, processing_message, chunk_strategy）
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    UPDATE_DOCUMENT_STATUS,
                    (
                        kwargs.get("status", "ready"),
                        kwargs.get("chunk_count", 0),
                        kwargs.get("error_msg", ""),
                        kwargs.get("processing_state"),
                        kwargs.get("processing_progress", 0),
                        kwargs.get("processing_message"),
                        kwargs.get("chunk_strategy"),
                        doc_id,
                    ),
                )
            await conn.commit()

    async def soft_delete_document(self, doc_id: str) -> bool:
        """软删除单个文档。

        Args:
            doc_id: 文档 UUID

        Returns:
            是否更新了记录
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SOFT_DELETE_DOCUMENT_BY_ID, (doc_id,))
                deleted = cursor.rowcount
            await conn.commit()
        return deleted > 0

    async def soft_delete_documents_by_kb(self, kb_id: str) -> int:
        """批量软删除指定知识库的所有文档。

        Args:
            kb_id: 知识库 UUID

        Returns:
            被软删除的记录数
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SOFT_DELETE_DOCUMENTS_BY_KB_ID, (kb_id,))
                count = cursor.rowcount
            await conn.commit()
        return count
