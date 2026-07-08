"""文档上传与列表端点。

异步上传：立即返回 202，后台处理文档。
状态轮询：POST /api/kbs/documents/status
分块预览：POST /api/kbs/documents/chunks
"""

import asyncio
import hashlib
import os
import tempfile
import uuid

from fastapi import APIRouter, File, Form, Request, UploadFile
from loguru import logger
from pydantic import BaseModel

from src.app_service import AppService
from src.config.response_codes import Code
from src.infra.errors import BusinessError, SystemError
from src.infra.chunking.router import ChunkRouter
from src.infra.chunking.validator import ChunkData, validate_chunks
from src.infra.db.file_store import FileStore

router = APIRouter()

_service: AppService | None = None
_process_semaphore = asyncio.Semaphore(3)


def _get_service() -> AppService:
    """获取 AppService 单例实例。

    Returns:
        AppService 全局唯一实例
    """
    global _service
    if _service is None:
        _service = AppService()
    return _service


MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 单文件上传上限 10MB
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}


class DocumentListRequest(BaseModel):
    """文档列表请求体。"""

    kb_id: str


class DocumentStatusRequest(BaseModel):
    """文档状态请求体。"""

    kb_id: str
    doc_id: str


class DocumentChunksRequest(BaseModel):
    """分块预览请求体。"""

    kb_id: str
    doc_id: str
    page: int = 1
    page_size: int = 50


class UploadDocumentResponse(BaseModel):
    """文档上传响应。"""

    doc_id: str
    status: str
    filename: str
    dedup: bool = False


class DocumentListResponse(BaseModel):
    """文档列表项。"""
    id: str
    filename: str
    file_type: str
    file_size: int
    status: str
    created_at: str
    chunk_count: int = 0


class DocumentStatusResponse(BaseModel):
    """文档处理状态响应。"""
    status: str
    chunk_count: int = 0
    progress: int = 0
    error: str = ""
    processing_state: str | None = None
    processing_progress: int = 0
    processing_message: str = ""


class ChunkItem(BaseModel):
    """分块预览项。"""
    chunk_id: str
    content: str
    page: int = 1
    tokens: int = 0
    char_count: int
    block_type: str = "text"
    parent_content: str | None = None


class ChunksResponse(BaseModel):
    """分块预览响应。"""
    items: list[ChunkItem]
    total: int
    page: int
    page_size: int


class DocumentDeleteResponse(BaseModel):
    """文档删除响应。"""
    success: bool


@router.post("/kbs/documents/list")
async def get_documents(body: DocumentListRequest, request: Request = None) -> list[DocumentListResponse]:
    """列出知识库中的所有文档。

    Args:
        body: 文档列表请求体，含 kb_id

    Returns:
        list[DocumentListResponse]: 文档列表
    """
    svc = _get_service()
    docs = await svc.get_documents(body.kb_id)
    logger.info("Documents list: kb_id={} count={}", body.kb_id, len(docs))
    return [DocumentListResponse(**d) for d in docs]


@router.post("/kbs/documents/upload", status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form(...),
    request: Request = None,
) -> UploadDocumentResponse:
    """上传文档并立即返回（异步处理）。

    文档在后台经历：解析 → 分块 → 入库 → ready/failed。
    可通过 POST /api/kbs/documents/status 轮询进度。

    Args:
        file: 上传的文件对象（pdf / docx / txt）
        kb_id: 目标知识库 UUID
        request: FastAPI 请求对象（用于获取用户上下文）

    Returns:
        dict: 含 doc_id、status、filename、dedup 的立即返回结果

    Raises:
        BusinessError 413: 文件超过 10MB 上限
        BusinessError 400: 不支持的文件类型
        SystemError 500: 上传到存储服务失败
    """
    user_id = getattr(request.state, "user_id", "") if request else ""
    contents = await file.read()
    logger.info(
        "Upload request: filename={} size={} kb_id={} user_id={}",
        file.filename,
        len(contents),
        kb_id,
        user_id[:8] + "..." if user_id else "",
    )
    if len(contents) > MAX_UPLOAD_SIZE:
        logger.warning(
            "Upload rejected (too large): filename={} size={} max={}",
            file.filename,
            len(contents),
            MAX_UPLOAD_SIZE,
        )
        raise BusinessError(Code.FILE_TOO_LARGE, Code.FILE_TOO_LARGE_MSG, 413)

    ext = (
        f".{file.filename.rsplit('.', 1)[-1].lower()}"
        if "." in (file.filename or "")
        else ""
    )
    if ext not in ALLOWED_EXTENSIONS:
        logger.warning(
            "Upload rejected (unsupported type): filename={} ext={}", file.filename, ext
        )
        raise BusinessError(Code.FILE_TYPE_UNSUPPORTED, Code.FILE_TYPE_UNSUPPORTED_MSG, 400)

    svc = _get_service()

    # MD5 去重：相同 KB 内不允许重复文件
    file_hash = hashlib.md5(contents).hexdigest()
    docs = await svc.db.get_documents(kb_id)
    for d in docs:
        if d.get("hash") == file_hash:
            logger.info(
                "Duplicate document detected: {} (hash={})", file.filename, file_hash
            )
            return UploadDocumentResponse(
                doc_id=d["id"], status=d["status"], filename=d["filename"], dedup=True,
            )

    # 先写入 MinIO 存储
    doc_id = str(uuid.uuid4())
    file_type = ext.lstrip(".")
    minio_key = FileStore.build_path(user_id, kb_id, doc_id, file.filename)
    fs = FileStore()
    if not await asyncio.to_thread(fs.upload, minio_key, contents):
        logger.error(
            "Upload failed (MinIO): filename={} key={}", file.filename, minio_key
        )
        raise SystemError(Code.FILE_UPLOAD_FAILED, Code.FILE_UPLOAD_FAILED_MSG, 500)

    # 再写入 MySQL 元信息
    await svc.db.add_document(
        doc_id,
        kb_id,
        file.filename,
        file_type,
        len(contents),
        user_id=user_id,
        status="processing",
        processing_state="extracting",
        processing_progress=0,
        file_path=minio_key,
        hash=file_hash,
    )

    # 启动后台处理任务
    asyncio.create_task(
        _process_document_task(svc, kb_id, doc_id, minio_key, file.filename, ext)
    )

    logger.info(
        "Upload success: doc_id={} filename={} kb_id={} size={}",
        doc_id,
        file.filename,
        kb_id,
        len(contents),
    )

    return UploadDocumentResponse(doc_id=doc_id, status="processing", filename=file.filename)


async def _process_document_task(
    svc: AppService, kb_id: str, doc_id: str, minio_key: str, filename: str, ext: str
) -> None:
    """在后台处理文档：从 MinIO 下载 → 解析 → 分块 → 入库（完全异步版）。

    每个同步操作均通过 asyncio.to_thread 委托到线程池执行，
    DB 调用直接 await 异步方法，确保不阻塞事件循环。

    Args:
        svc: AppService 实例
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
            await svc.db.update_document_status(
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
            parse_result = await asyncio.to_thread(svc.router.parse, tmp_path)
            if parse_result.is_scanned:
                await svc.db.update_document_status(
                    doc_id, "failed", error_msg="扫描件暂不支持"
                )
                logger.warning("Scanned document detected: {}", filename)
                return

            # 分块 — CPU，to_thread
            full_text = "\n".join(c.content for c in parse_result.chunks)
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

            # 分块质量校验 — CPU，to_thread
            chunk_data_list = [
                ChunkData(content=c["content"], metadata=c["metadata"]) for c in chunks
            ]
            quality = await asyncio.to_thread(validate_chunks, chunk_data_list)
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

            # ChromaDB — 同步库，to_thread
            count = await asyncio.to_thread(
                svc.vector_store.add_chunks, kb_id, chunk_data_list, doc_id
            )

            # DB 更新 — 异步，直接 await
            await svc.db.update_document_status(
                doc_id,
                "ready",
                chunk_count=count,
                processing_state="completed",
                processing_progress=100,
                processing_message=f"处理完成，共 {count} 个分块",
                chunk_strategy=strategy,
            )
            logger.info(
                "Document processed: {} -> {} chunks (strategy={})",
                filename,
                count,
                strategy,
            )

        except Exception as e:
            error_msg = str(e)
            logger.error("Document processing failed: {} - {}", filename, error_msg)
            await svc.db.update_document_status(doc_id, "failed", error_msg=error_msg)
        finally:
            if tmp_path:
                await asyncio.to_thread(os.unlink, tmp_path)


@router.post("/kbs/documents/status")
async def get_document_status(body: DocumentStatusRequest) -> DocumentStatusResponse:
    """获取文档的处理状态。

    Args:
        body: 文档状态请求体，含 kb_id 和 doc_id

    Returns:
        DocumentStatusResponse: 含 status、chunk_count、progress、error 以及处理阶段详情
    """
    svc = _get_service()
    docs = await svc.db.get_documents(body.kb_id)
    doc = next((d for d in docs if d["id"] == body.doc_id), None)
    if not doc:
        return DocumentStatusResponse(status="not_found")
    return DocumentStatusResponse(
        status=doc["status"],
        chunk_count=doc.get("chunk_count", 0),
        progress=doc.get("processing_progress", 0),
        error=doc.get("error_msg", ""),
        processing_state=doc.get("processing_state"),
        processing_progress=doc.get("processing_progress", 0),
        processing_message=doc.get("processing_message", ""),
    )


@router.post("/kbs/documents/chunks")
async def get_document_chunks(body: DocumentChunksRequest) -> ChunksResponse:
    """分页预览已处理文档的分块内容。

    Args:
        body: 分块预览请求体，含 kb_id、doc_id、page、page_size

    Returns:
        ChunksResponse: 含 items（当前页分块列表）、total（总量）、page、page_size
    """
    svc = _get_service()
    result = await asyncio.to_thread(
        svc.vector_store.get_chunks_paginated,
        body.doc_id,
        body.kb_id,
        page=body.page,
        page_size=body.page_size,
    )
    items = [
        ChunkItem(
            chunk_id=c["id"],
            content=c["content"][:500],
            page=c.get("metadata", {}).get("page", 1),
            tokens=c.get("metadata", {}).get("tokens", 0),
            char_count=len(c["content"]),
            block_type=c.get("metadata", {}).get("block_type", "text"),
            parent_content=c.get("metadata", {}).get("parent_content"),
        )
        for c in result["items"]
    ]
    return ChunksResponse(
        items=items, total=result["total"], page=result["page"], page_size=result["page_size"],
    )


class DocumentDeleteRequest(BaseModel):
    """文档删除请求体。"""

    kb_id: str
    doc_id: str


@router.post("/kbs/documents/delete")
async def delete_document(body: DocumentDeleteRequest) -> DocumentDeleteResponse:
    """软删除文档（标记为 deleted），同时删除向量库中的分块。

    Args:
        body: 文档删除请求体，含 kb_id 和 doc_id

    Returns:
        DocumentDeleteResponse: 含 success 布尔值
    """
    svc = _get_service()
    ok = await svc.db.soft_delete_document(body.doc_id)
    if ok:
        svc.vector_store.delete_document(body.kb_id, body.doc_id)
        logger.info("Document deleted: kb_id={} doc_id={}", body.kb_id, body.doc_id)
    return DocumentDeleteResponse(success=ok)
