"""文档处理服务 — 文档的查询、删除、上传处理流水线。"""

import asyncio
import os
import uuid

from loguru import logger

from src.infra.db.mysql_db import MySQLDB
from src.infra.db.vector_store import VectorStore
from src.infra.chunking.validator import ChunkData, validate_chunks
from src.infra.errors import BusinessError
from src.config.response_codes import Code
from src.parsers.router import DocRouter


class DocumentService:
    """文档 CRUD 及处理流水线。

    包含文档的增删查改，以及从解析到向量化入库的完整流水线。
    """

    def __init__(
        self,
        db: MySQLDB,
        vector_store: VectorStore,
        router: DocRouter,
    ) -> None:
        self.db = db
        self.vector_store = vector_store
        self.router = router

    async def get_documents(self, kb_id: str) -> list[dict]:
        """获取知识库下的文档列表。"""
        return await self.db.get_documents(kb_id)

    async def delete_document(self, kb_id: str, doc_id: str, user_id: str) -> dict:
        """删除文档（合法性校验 + ChromaDB 清理 + MySQL 软删除）。"""
        doc = await self.db.get_document(doc_id)
        if not doc:
            raise BusinessError(Code.DOC_NOT_FOUND, Code.DOC_NOT_FOUND_MSG, 404)
        if doc["user_id"] != user_id:
            raise BusinessError(
                Code.DOC_DELETE_NOT_ALLOWED,
                Code.DOC_DELETE_NOT_ALLOWED_MSG,
                403,
            )
        if doc["status"] not in ("ready", "failed"):
            raise BusinessError(
                Code.DOC_STATUS_CONFLICT,
                Code.DOC_STATUS_CONFLICT_MSG,
                409,
            )
        try:
            await asyncio.to_thread(self.vector_store.delete_document, kb_id, doc_id)
        except Exception:
            logger.warning("ChromaDB delete failed for doc_id={}, will retry", doc_id)
        deleted = await self.db.soft_delete_document(doc_id)
        if not deleted:
            raise BusinessError(Code.DOC_NOT_FOUND, Code.DOC_NOT_FOUND_MSG, 404)
        logger.info("Document deleted: {} ({})", doc["filename"], doc_id)
        return {"doc_id": doc_id, "filename": doc["filename"], "status": "deleted"}

    def upload_and_process(
        self, kb_id: str, file_path: str, filename: str
    ) -> dict:
        """上传文档并执行完整处理流水线。

        同步执行，预计耗时 1-30 秒。
        """
        file_type = (
            filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
        )
        file_size = 0
        try:
            file_size = os.path.getsize(file_path)
        except OSError as e:
            logger.warning("Cannot get file size for '{}': {}", filename, e)

        doc_id = str(uuid.uuid4())
        self.db.add_document(doc_id, kb_id, filename, file_type, file_size)

        try:
            parse_result = self.router.parse(file_path)
            for chunk in parse_result.chunks:
                chunk.metadata["source"] = filename

            if parse_result.is_scanned:
                error_msg = "文档为扫描件或无可提取文本，MVP 暂不支持 OCR"
                self.db.update_document_status(doc_id, "failed", error_msg=error_msg)
                logger.warning("Scanned document detected: {}", filename)
                return {"success": False, "chunk_count": 0, "error": error_msg}

            chunk_data_list = [
                ChunkData(content=c.content, metadata=c.metadata)
                for c in parse_result.chunks
            ]
            quality_report = validate_chunks(chunk_data_list)
            if quality_report.tiny_chunks:
                logger.warning(
                    "Document '{}' has {} tiny chunks",
                    filename,
                    len(quality_report.tiny_chunks),
                )
            if quality_report.garbled_chunks:
                logger.warning(
                    "Document '{}' has {} garbled chunks",
                    filename,
                    len(quality_report.garbled_chunks),
                )

            chunk_count = self.vector_store.add_chunks(
                kb_id, parse_result.chunks, doc_id
            )
            self.db.update_document_status(doc_id, "ready", chunk_count=chunk_count)
            logger.info("Document processed: {} -> {} chunks", filename, chunk_count)
            return {"success": True, "chunk_count": chunk_count, "error": ""}

        except Exception as e:
            error_msg = str(e)
            logger.exception(
                "Document processing failed: {} - {}", filename, error_msg
            )
            try:
                self.db.update_document_status(
                    doc_id, "failed", error_msg=error_msg
                )
            except Exception:
                logger.exception(
                    "Failed to update document status after processing error",
                )
            return {"success": False, "chunk_count": 0, "error": error_msg}

    # ── 以下为异步版后台任务的方法 ──

    def enrich_chunk_pages(
        self, chunks: list[dict], parse_chunks: list, full_text: str
    ) -> None:
        """从解析器分块反推 chunk 页码。"""
        offset = 0
        page_map = []
        for c in parse_chunks:
            page = c.metadata.get("page", 1)
            page_map.append((offset, offset + len(c.content), page))
            offset += len(c.content) + 2
        for chunk in chunks:
            text = chunk["content"]
            pos = full_text.find(text)
            if pos < 0:
                continue
            end = pos + len(text)
            pages = {p for s, e, p in page_map if s < end and e > pos}
            chunk["metadata"]["page"] = min(pages)
