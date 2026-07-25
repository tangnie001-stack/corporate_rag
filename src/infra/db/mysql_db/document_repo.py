"""文档 Repo — document 表 CRUD。"""

from src.config.queries import (
    INSERT_DOCUMENT, SELECT_DOCUMENTS_BY_KB_ID, SELECT_DOC_NAMES_BY_IDS,
    UPDATE_DOCUMENT_STATUS, SOFT_DELETE_DOCUMENT_BY_ID, SOFT_DELETE_DOCUMENTS_BY_KB_ID,
)
from src.infra.db.entities import DocEntity


class DocumentRepo:
    def __init__(self, mysql_db):
        self._pool_getter = mysql_db._get_pool

    async def add_document(self, doc: DocEntity) -> None:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(INSERT_DOCUMENT, (
                    doc.id, doc.kb_id, doc.user_id, doc.filename, doc.file_type,
                    doc.file_size, doc.status, doc.file_path, doc.hash,
                    doc.processing_state, doc.processing_progress, doc.processing_message,
                    doc.chunk_strategy, doc.meta_info,
                ))
            await conn.commit()

    async def get_documents(self, kb_id: str) -> list[DocEntity]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_DOCUMENTS_BY_KB_ID, (kb_id,))
                rows = await cursor.fetchall()
        return [DocEntity(**row) for row in rows]

    async def get_doc_names(self, doc_ids: list[str]) -> dict[str, str]:
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
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(UPDATE_DOCUMENT_STATUS, (
                    kwargs.get("status", "ready"),
                    kwargs.get("chunk_count", 0),
                    kwargs.get("error_msg", ""),
                    kwargs.get("processing_state"),
                    kwargs.get("processing_progress", 0),
                    kwargs.get("processing_message"),
                    kwargs.get("chunk_strategy"),
                    doc_id,
                ))
            await conn.commit()

    async def soft_delete_document(self, doc_id: str) -> bool:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SOFT_DELETE_DOCUMENT_BY_ID, (doc_id,))
                deleted = cursor.rowcount
            await conn.commit()
        return deleted > 0

    async def soft_delete_documents_by_kb(self, kb_id: str) -> int:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SOFT_DELETE_DOCUMENTS_BY_KB_ID, (kb_id,))
                count = cursor.rowcount
            await conn.commit()
        return count
