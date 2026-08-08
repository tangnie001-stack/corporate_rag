"""文档处理服务 — 文档的查询、删除、上传处理流水线。"""

import asyncio
import os
import tempfile
import time

from loguru import logger

from src.chunking.router import ChunkRouter
from src.chunking.scorer import ChunkQualityScorer
from src.chunking.strategies.base import BaseChunker
from src.chunking.validator import ChunkData, validate_chunks
from src.config import CHUNK_EVAL_ENABLED
from src.config.response_codes import Code
from src.infra.db.file_store import FileStore
from src.infra.db.models.document import DocModel as DocEntity
from src.infra.db.mysql_db import DocumentRepo
from src.infra.db.vector_store import VectorStore
from src.models import get_embeddings
from src.parsers.router import DocRouter
from src.utils.errors import BusinessError

_process_semaphore = asyncio.Semaphore(3)


def _merge_tiny_chunks(
    chunks: list[ChunkData],
    strategy: str = "",
    min_tokens: int = 50,
) -> list[ChunkData]:
    """将 tokens < min_tokens 的 tiny chunk 合并到前一个 chunk。

    仅对 parent_child 和 table_preserving 策略生效。
    qa 策略的 chunk 是完整问答对，合并会破坏语义结构，跳过。

    Args:
        chunks: chunker.chunk() 输出的 chunk 列表
        strategy: 当前文档的分块策略
        min_tokens: tiny chunk 判定阈值

    Returns:
        合并后的 chunk 列表
    """
    if strategy not in ("parent_child", "table_preserving"):
        return chunks

    merged: list[ChunkData] = []
    for c in chunks:
        tokens = c.tokens or BaseChunker.count_tokens(c.content)
        if tokens < min_tokens and merged:
            merged[-1].content += "\n" + c.content
            merged[-1].tokens = BaseChunker.count_tokens(merged[-1].content)
        else:
            merged.append(c)
    return merged


class DocumentService:
    """文档 CRUD 及处理流水线。

    包含文档的增删查改，以及从解析到向量化入库的完整流水线。
    """

    def __init__(
        self,
        doc_repo: DocumentRepo,
        vector_store: VectorStore,
        router: DocRouter,
    ) -> None:
        self._doc_repo = doc_repo
        self.vector_store = vector_store
        self.router = router

    async def get_documents(self, kb_id: str) -> list[dict]:
        """获取知识库下的文档列表。"""
        docs = await self._doc_repo.get_documents(kb_id)
        return [
            {
                "id": d.id,
                "kb_id": d.kb_id,
                "filename": d.filename,
                "file_type": d.file_type,
                "file_size": d.file_size,
                "user_id": d.user_id,
                "status": d.status,
                "file_path": d.file_path,
                "hash": d.hash,
                "processing_state": d.processing_state,
                "processing_progress": d.processing_progress,
                "processing_message": d.processing_message,
                "chunk_strategy": d.chunk_strategy,
                "chunk_count": d.chunk_count,
                "error_msg": d.error_msg,
                "meta_info": d.meta_info,
                "created_at": d.created_at,
            }
            for d in docs
        ]

    async def delete_document(self, kb_id: str, doc_id: str, user_id: str) -> dict:
        """删除文档（合法性校验 + ChromaDB 清理 + MySQL 软删除）。"""
        doc = await self._doc_repo.get_document(doc_id)
        if not doc:
            raise BusinessError(Code.DOC_NOT_FOUND, Code.DOC_NOT_FOUND_MSG, 404)
        if doc.user_id != user_id:
            raise BusinessError(
                Code.DOC_DELETE_NOT_ALLOWED,
                Code.DOC_DELETE_NOT_ALLOWED_MSG,
                403,
            )
        if doc.status not in ("ready", "failed"):
            raise BusinessError(
                Code.DOC_STATUS_CONFLICT,
                Code.DOC_STATUS_CONFLICT_MSG,
                409,
            )
        try:
            await asyncio.to_thread(self.vector_store.delete_document, kb_id, doc_id)
        except Exception:  # noqa: BLE001
            logger.warning("ChromaDB delete failed for doc_id={}, will retry", doc_id)
        deleted = await self._doc_repo.soft_delete_document(doc_id)
        if not deleted:
            raise BusinessError(Code.DOC_NOT_FOUND, Code.DOC_NOT_FOUND_MSG, 404)
        logger.info("Document deleted: {} ({})", doc.filename, doc_id)
        return {"doc_id": doc_id, "filename": doc.filename, "status": "deleted"}

    async def store_and_process(
        self,
        kb_id: str,
        filename: str,
        content: bytes,
        ext: str,
        user_id: str = "",
    ) -> dict:
        """封装文件上传后的全流程：校验 → 去重 → MinIO 上传 → DB 写入 → 后台处理。

        Args:
            kb_id: 知识库 UUID
            filename: 原始文件名
            content: 文件二进制内容
            ext: 文件扩展名（如 .pdf, .docx, .txt）
            user_id: 用户 ID（用于构建 MinIO 路径）

        Returns:
            dict: 包含 doc_id, status, filename 和可选的 dedup 信息

        Raises:
            ValidationError: 文件类型不支持或文件过大
        """
        from src.config import MAX_FILE_SIZE
        from src.infra.db.file_store import FileStore

        # 1. 文件大小校验
        if len(content) > MAX_FILE_SIZE:
            from src.utils.errors import ValidationError

            raise ValidationError("FILE_TOO_LARGE", "文件大小超过限制", 413)

        # 2. 文件类型校验
        ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
        if ext.lower() not in ALLOWED_EXTENSIONS:
            from src.utils.errors import ValidationError

            raise ValidationError("FILE_TYPE_UNSUPPORTED", "不支持的文件类型", 400)

        # 3. MD5 去重
        import hashlib

        file_hash = hashlib.md5(content).hexdigest()
        existing_docs = await self._doc_repo.get_documents(kb_id)
        for doc in existing_docs:
            if doc.hash == file_hash:
                logger.info(
                    "Duplicate document detected: hash={} existing_doc_id={}",
                    file_hash,
                    doc.id,
                )
                return {
                    "doc_id": doc.id,
                    "filename": filename,
                    "dedup": True,
                    "status": doc.status,
                }

        # 4. 生成 doc_id 并上传到 MinIO
        import uuid

        doc_id = str(uuid.uuid4())
        minio_key = FileStore.build_path(user_id, kb_id, doc_id, filename)
        file_store = FileStore()
        ok = await asyncio.to_thread(file_store.upload, minio_key, content)
        if not ok:
            from src.utils.errors import SystemError

            raise SystemError("FILE_UPLOAD_FAILED", "文件上传到存储服务失败", 500)

        # 5. 写入 MySQL 元信息
        await self._doc_repo.add_document(
            DocEntity(
                id=doc_id,
                kb_id=kb_id,
                filename=filename,
                file_type=ext.lstrip("."),
                file_size=len(content),
                user_id=user_id,
                status="processing",
                processing_state="extracting",
                processing_progress=0,
                file_path=minio_key,
                hash=file_hash,
            )
        )

        # 6. 启动后台处理任务
        asyncio.create_task(
            self.process_document(kb_id, doc_id, minio_key, filename, ext)
        )

        logger.info(
            "Document submitted for processing: doc_id={} kb_id={} filename={}",
            doc_id,
            kb_id,
            filename,
        )
        return {"doc_id": doc_id, "status": "processing", "filename": filename}

    # ── 以下为异步版后台任务的方法 ──

    def enrich_chunk_pages(
        self, chunks: list[ChunkData], parse_chunks: list, full_text: str
    ) -> None:
        """从解析器分块反推 chunk 页码。"""
        offset = 0
        page_map = []
        for c in parse_chunks:
            page = c.metadata.get("page", 1)
            page_map.append((offset, offset + len(c.content), page))
            offset += len(c.content) + 2
        for chunk in chunks:
            text = chunk.content
            pos = full_text.find(text)
            if pos < 0:
                continue
            end = pos + len(text)
            pages = {p for s, e, p in page_map if s < end and e > pos}
            chunk.metadata["page"] = min(pages)

    def _enrich_chunk_pages(
        self, chunks: list[ChunkData], parse_chunks: list, full_text: str
    ) -> None:
        """从解析器分块反推 chunk 页码（私有版本，供 process_document 调用）。"""
        offset = 0
        page_map = []
        for c in parse_chunks:
            page = c.metadata.get("page", 1)
            page_map.append((offset, offset + len(c.content), page))
            offset += len(c.content) + 2
        for chunk in chunks:
            text = chunk.content
            pos = full_text.find(text)
            if pos < 0:
                continue
            end = pos + len(text)
            pages = {p for s, e, p in page_map if s < end and e > pos}
            chunk.metadata["page"] = min(pages)

    def _merge_tiny_chunks(
        self,
        chunks: list[ChunkData],
        strategy: str = "",
        min_tokens: int = 50,
    ) -> list[ChunkData]:
        """将 tokens < min_tokens 的 tiny chunk 合并到前一个 chunk。

        仅对 parent_child 和 table_preserving 策略生效。
        qa 策略的 chunk 是完整问答对，合并会破坏语义结构，跳过。

        Args:
            chunks: chunker.chunk() 输出的 chunk 列表
            strategy: 当前文档的分块策略
            min_tokens: tiny chunk 判定阈值

        Returns:
            合并后的 chunk 列表
        """
        if strategy not in ("parent_child", "table_preserving"):
            return chunks

        merged: list[ChunkData] = []
        for c in chunks:
            tokens = c.tokens or BaseChunker.count_tokens(c.content)
            if tokens < min_tokens and merged:
                merged[-1].content += "\n" + c.content
                merged[-1].tokens = BaseChunker.count_tokens(merged[-1].content)
            else:
                merged.append(c)
        return merged

    async def process_document(
        self,
        kb_id: str,
        doc_id: str,
        minio_key: str,
        filename: str,
        ext: str,
    ) -> None:
        """后台异步处理文档：下载 → 解析 → 分块 → 向量化入库。

        每个同步操作均通过 asyncio.to_thread 委托到线程池执行，
        DB 调用直接 await 异步方法，确保不阻塞事件循环。

        Args:
            kb_id: 知识库 UUID
            doc_id: 文档 UUID
            minio_key: MinIO 存储路径
            filename: 文件名
            ext: 文件扩展名（含点号，如 .pdf）
        """
        async with _process_semaphore:
            tmp_path = None
            try:
                # DB 是异步的 — 直接 await
                await self._doc_repo.update_document_status(
                    doc_id,
                    "processing",
                    processing_state="extracting",
                    processing_progress=0,
                )

                # MinIO 下载 — 同步库，to_thread
                contents = await asyncio.to_thread(FileStore().download, minio_key)
                if contents is None:
                    raise RuntimeError(f"无法从 MinIO 下载文档: {minio_key}")

                # 临时文件 — 同步 I/O，to_thread
                tmp = await asyncio.to_thread(
                    tempfile.NamedTemporaryFile, delete=False, suffix=ext
                )
                tmp_path = tmp.name
                await asyncio.to_thread(tmp.write, contents)
                await asyncio.to_thread(tmp.close)

                # 解析 — CPU + 文件 I/O，to_thread
                t0 = time.perf_counter()
                parse_result = await asyncio.to_thread(self.router.parse, tmp_path)
                logger.info(
                    "Parser result: {} -> type={} pages={} chars={} scanned={} encoding={}",
                    filename,
                    parse_result.file_type,
                    parse_result.total_pages,
                    parse_result.total_chars,
                    parse_result.is_scanned,
                    parse_result.encoding,
                )
                if parse_result.is_scanned:
                    await self._doc_repo.update_document_status(
                        doc_id, "failed", error_msg="扫描件暂不支持"
                    )
                    logger.warning("Scanned document detected: {}", filename)
                    return

                # 分块 — CPU，to_thread
                t1 = time.perf_counter()
                full_text = "\n\n".join(c.content for c in parse_result.chunks)
                strategy = await asyncio.to_thread(
                    ChunkRouter.detect_strategy, full_text, parse_result.chunks
                )
                chunker = await asyncio.to_thread(ChunkRouter.get_chunker, strategy)
                logger.info(
                    "Detected chunk strategy '{}' for document: {}", strategy, filename
                )
                chunks = await asyncio.to_thread(
                    chunker.chunk, full_text, {"source": filename, "doc_id": doc_id}
                )

                # 从解析器分块反补 chunk 页码
                self._enrich_chunk_pages(chunks, parse_result.chunks, full_text)

                # 合并 tiny chunk — 将 < 50 tokens 的碎片合并到前一个 chunk
                chunks = self._merge_tiny_chunks(chunks, strategy)

                # 分块质量校验 — CPU，to_thread
                quality = await asyncio.to_thread(validate_chunks, chunks)
                if quality.tiny_chunks:
                    logger.warning(
                        "Document '{}' has {} tiny chunks",
                        filename,
                        len(quality.tiny_chunks),
                    )
                if quality.garbled_chunks:
                    logger.warning(
                        "Document '{}' has {} garbled chunks",
                        filename,
                        len(quality.garbled_chunks),
                    )

                # 分块质量评估 — 开关控制，只记录不拦截
                chunk_embeddings = None
                if CHUNK_EVAL_ENABLED:
                    try:
                        # 预计算 embedding，一次计算两处复用（评估 SBR + ChromaDB 入库）
                        chunk_embeddings = await asyncio.to_thread(
                            get_embeddings().embed_documents,
                            [c.content for c in chunks],
                        )
                        scorer = ChunkQualityScorer()
                        eval_result = await asyncio.to_thread(
                            scorer.evaluate,
                            chunks,
                            filename,
                            strategy,
                            chunk_embeddings,
                        )
                        await self._doc_repo.update_document_meta_info(
                            doc_id, {"eval": eval_result}
                        )
                        logger.info(
                            "Chunk eval for '{}': score={} passed={}",
                            filename,
                            eval_result.get("overall_score"),
                            eval_result.get("passed"),
                        )
                    except Exception as eval_err:  # noqa: BLE001
                        logger.warning(
                            "Chunk eval failed for '{}': {}", filename, eval_err
                        )

                # ChromaDB — 同步库，to_thread
                t2 = time.perf_counter()
                count = await asyncio.to_thread(
                    self.vector_store.add_chunks,
                    kb_id,
                    chunks,
                    doc_id,
                    chunk_embeddings,
                )

                # DB 更新 — 异步，直接 await
                t3 = time.perf_counter()
                await self._doc_repo.update_document_status(
                    doc_id,
                    "ready",
                    chunk_count=count,
                    processing_state="completed",
                    processing_progress=100,
                    processing_message=f"处理完成，共 {count} 个分块",
                    chunk_strategy=strategy,
                )
                logger.info(
                    "Document processed: {} -> {} chunks (strategy={}) | "
                    "parse={:.1f}s chunk={:.1f}s store={:.1f}s total={:.1f}s",
                    filename,
                    count,
                    strategy,
                    t1 - t0,
                    t2 - t1,
                    t3 - t2,
                    t3 - t0,
                )

            except Exception as e:  # noqa: BLE001
                error_msg = str(e)[:1024]
                logger.exception(
                    "Document processing failed: {} - {}",
                    filename,
                    error_msg,
                )
                await self._doc_repo.update_document_status(
                    doc_id, "failed", error_msg=error_msg
                )
            finally:
                if tmp_path:
                    await asyncio.to_thread(os.unlink, tmp_path)
