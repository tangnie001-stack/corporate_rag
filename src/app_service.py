"""应用业务逻辑层 -- UI 与后端之间的薄封装。

职责：
  1. 知识库 CRUD（list / create / delete）
  2. 文档上传、解析、向量化全流程
  3. 对话问答（RAG 链路）+ 历史保存
  4. 文档列表查询

设计原则：
  - 大多数方法为 async，直接 await 异步 DB 调用
  - upload_and_process() 保持同步，在独立线程中执行（由 _process_document_task 管理）
  - 所有外部依赖可在构造时注入（方便测试）
"""

import asyncio
import os
import uuid
from typing import Optional

import redis.asyncio as redis_async
from loguru import logger

from src.config import REDIS_URL
from src.infra.db.mysql_db import MySQLDB
from src.parsers.router import DocRouter
from src.rag.chain import RAGChain, RAGContext
from src.infra.chunking.validator import ChunkData, validate_chunks
from src.infra.db.vector_store import VectorStore
from src.config.response_codes import Code
from src.infra.errors import BusinessError


class AppService:
    """UI 与后端之间的业务逻辑层。

    封装所有面向 UI 的业务操作，包括知识库管理、文档处理和 RAG 问答。
    所有外部依赖均可在构造时注入，便于单元测试中 mock。
    """

    def __init__(
        self,
        mysql_db: Optional[MySQLDB] = None,
        vector_store: Optional[VectorStore] = None,
        router: Optional[DocRouter] = None,
        rag_chain: Optional[RAGChain] = None,
    ) -> None:
        """初始化 AppService。

        如果未传入依赖实例，则创建默认实例。

        Args:
            mysql_db: MySQL 数据库实例，默认创建新的 MySQLDB
            vector_store: 向量存储实例，默认创建新的 VectorStore
            router: 文档解析路由器，默认创建新的 DocRouter
            rag_chain: RAG 问答链，默认创建新的 RAGChain
        """
        self.db = mysql_db or MySQLDB()
        self.vector_store = vector_store or VectorStore()
        self.router = router or DocRouter()
        self.rag_chain = rag_chain or RAGChain()
        self._redis = redis_async.from_url(REDIS_URL)

    @property
    def redis_client(self):
        """获取 Redis 客户端实例。"""
        return self._redis

    # ==================== 知识库管理 ====================

    async def list_knowledge_bases(self, user_id: str = "") -> list[dict]:
        """列出所有知识库（含文档计数）。"""
        return await self.db.get_all_kb(user_id)

    async def create_knowledge_base(
        self,
        name: str,
        description: str = "",
        user_id: str = "",
    ) -> tuple[str, bool]:
        """创建知识库。

        Args:
            name: 知识库名称
            description: 知识库描述（可选）
            user_id: 用户 ID（可选）

        Returns:
            (kb_id, is_new) 元组：kb_id 为知识库 UUID，is_new 表示是否新创建
        """
        return await self.db.get_or_create_kb(user_id, name, description)

    async def delete_knowledge_base(self, kb_id: str) -> tuple[bool, str]:
        """软删除知识库（文档标 deleted → ChromaDB 删 collection → KB 标 deleted）。

        Args:
            kb_id: 知识库 UUID

        Returns:
            (成功标记, 消息) 元组
        """
        # 1. 软删除所有关联文档
        await self.db.soft_delete_documents_by_kb(kb_id)

        # 2. 删除 ChromaDB collection（防御性清理，collection 不存在不报错）
        try:
            await asyncio.to_thread(self.vector_store.delete_collection, kb_id)
            logger.info("ChromaDB delete_collection: kb_id={}", kb_id)
        except Exception:
            logger.warning("ChromaDB delete collection failed for kb={}", kb_id)

        # 3. 软删除知识库
        ok = await self.db.soft_delete_kb(kb_id)
        if ok:
            logger.info("Knowledge base soft-deleted: {}", kb_id)
            return True, "知识库已删除"
        logger.warning("Knowledge base '{}' not found for deletion", kb_id)
        return False, "知识库不存在"

    # ==================== 文档管理 ====================

    async def get_documents(self, kb_id: str) -> list[dict]:
        """获取指定知识库下的文档列表。

        Args:
            kb_id: 知识库 UUID

        Returns:
            文档信息字典列表，不存在时返回空列表
        """
        return await self.db.get_documents(kb_id)

    async def delete_document(self, kb_id: str, doc_id: str, user_id: str) -> dict:
        """删除文档：ChromaDB 删向量 → MySQL 标 deleted。

        Args:
            kb_id: 知识库 UUID
            doc_id: 文档 UUID
            user_id: 当前用户 ID（从 request.state 获取）

        Returns:
            {"doc_id": str, "filename": str, "status": "deleted"}

        Raises:
            BusinessError(Code.DOC_NOT_FOUND): 文档不存在
            BusinessError(Code.DOC_DELETE_NOT_ALLOWED): 非上传者
            BusinessError(Code.DOC_STATUS_CONFLICT): 状态不可删
        """
        # 1. 查文档
        doc = await self.db.get_document(doc_id)
        if not doc:
            raise BusinessError(Code.DOC_NOT_FOUND, Code.DOC_NOT_FOUND_MSG, 404)

        # 2. 校验权限
        if doc["user_id"] != user_id:
            raise BusinessError(
                Code.DOC_DELETE_NOT_ALLOWED,
                Code.DOC_DELETE_NOT_ALLOWED_MSG,
                403,
            )

        # 3. 校验状态
        if doc["status"] not in ("ready", "failed"):
            raise BusinessError(
                Code.DOC_STATUS_CONFLICT,
                Code.DOC_STATUS_CONFLICT_MSG,
                409,
            )

        # 4. ChromaDB 删向量（不在意结果——document 记录保留可重试）
        try:
            await asyncio.to_thread(self.vector_store.delete_document, kb_id, doc_id)
        except Exception:
            logger.warning("ChromaDB delete failed for doc_id={}, will retry", doc_id)

        # 5. MySQL 标 deleted
        deleted = await self.db.soft_delete_document(doc_id)
        if not deleted:
            raise BusinessError(Code.DOC_NOT_FOUND, Code.DOC_NOT_FOUND_MSG, 404)

        logger.info("Document deleted: {} ({})", doc["filename"], doc_id)
        return {
            "doc_id": doc_id,
            "filename": doc["filename"],
            "status": "deleted",
        }

    def upload_and_process(self, kb_id: str, file_path: str, filename: str) -> dict:
        """上传文档并执行完整处理流水线：解析 -> MySQL 记录 -> 向量化入库。

        此方法同步执行，预计耗时 1-30 秒。调用方（app.py）可在独立线程中运行。

        Args:
            kb_id: 知识库 UUID
            file_path: 上传文件的临时路径
            filename: 原始文件名（含扩展名）

        Returns:
            dict: {
                "success": bool,   -- 是否处理成功
                "chunk_count": int, -- 入库的分块数量
                "error": str,       -- 错误信息（成功时为空）
            }
        """
        # 提取文件类型和大小
        file_type = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
        file_size = 0
        try:
            file_size = os.path.getsize(file_path)
        except OSError as e:
            logger.warning("Cannot get file size for '{}': {}", filename, e)

        # Step 1: 写入 MySQL 文档记录（初始状态为 pending）
        doc_id = str(uuid.uuid4())
        self.db.add_document(doc_id, kb_id, filename, file_type, file_size)

        try:
            # Step 2: 解析文档
            parse_result = self.router.parse(file_path)

            # 用原始文件名覆写 source（解析器用的是临时文件路径）
            for chunk in parse_result.chunks:
                chunk.metadata["source"] = filename

            # Step 3: 检测扫描件（PDF 扫描件无可提取文本）
            if parse_result.is_scanned:
                error_msg = "文档为扫描件或无可提取文本，MVP 暂不支持 OCR"
                self.db.update_document_status(doc_id, "failed", error_msg=error_msg)
                logger.warning("Scanned document detected: {}", filename)
                return {"success": False, "chunk_count": 0, "error": error_msg}

            # Step 4: 分块质量校验
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

            # Step 5: 向量化入库（ChromaDB）
            chunk_count = self.vector_store.add_chunks(
                kb_id,
                parse_result.chunks,
                doc_id,
            )

            # Step 6: 更新文档状态为 ready
            self.db.update_document_status(doc_id, "ready", chunk_count=chunk_count)

            logger.info("Document processed: {} -> {} chunks", filename, chunk_count)
            return {"success": True, "chunk_count": chunk_count, "error": ""}

        except Exception as e:
            # 解析或入库过程中发生异常，记录失败状态
            error_msg = str(e)
            logger.exception("Document processing failed: {} - {}", filename, error_msg)
            try:
                self.db.update_document_status(doc_id, "failed", error_msg=error_msg)
            except Exception:
                logger.exception(
                    "Failed to update document status after processing error",
                )
            return {"success": False, "chunk_count": 0, "error": error_msg}

    # ==================== 问答 ====================

    def chat(
        self,
        kb_id: str,
        session_id: str,
        query: str,
    ) -> tuple[str, list[RAGContext]]:
        """执行一轮 RAG 问答。

        流程：
          1. 调用 RAGChain.chat_with_citations() 获取流式回答和引用
          2. 将流式 token 拼接为完整回答
          3. 将本轮回答保存到对话历史

        Args:
            kb_id: 知识库 UUID
            session_id: 会话 ID
            query: 用户问题

        Returns:
            (answer_text, citations_list) 元组
        """
        token_gen, citations = self.rag_chain.chat_with_citations(
            kb_id,
            session_id,
            query,
        )
        # 将流式 token 拼接为完整回答
        full_answer = "".join([t for t in token_gen])

        # 构建引用来源列表用于保存历史
        sources = [f"{c.source} (第{c.page}页)" for c in citations]
        self.rag_chain.chat_manager.add_message(
            session_id,
            "assistant",
            full_answer,
            sources=sources,
        )

        return full_answer, citations
