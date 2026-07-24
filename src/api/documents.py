"""文档上传与列表端点。

异步上传：立即返回 202，后台处理文档。
状态轮询：POST /api/kbs/documents/status
分块预览：POST /api/kbs/documents/chunks
"""

import asyncio
import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from loguru import logger

from src.api.model.request import (
    DocumentListRequest,
    DocumentStatusRequest,
    DocumentChunksRequest,
    DocumentDeleteRequest,
)
from src.api.model.response import (
    UploadDocumentResponse,
    DocumentListResponse,
    DocumentStatusResponse,
    ChunkItem,
    ChunksResponse,
    DocumentDeleteResponse,
)
from src.services.app_service import AppService
from src.api.dependencies import get_app_service
from src.config import MAX_FILE_SIZE, MAX_TABLE_TOKENS
from src.config.response_codes import Code
from src.utils.errors import BusinessError, SystemError
from src.infra.db.file_store import FileStore

router = APIRouter()

_process_semaphore = asyncio.Semaphore(3)


ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}


@router.post("/kbs/documents/list")
async def get_documents(
    body: DocumentListRequest,
    request: Request = None,
    svc: AppService = Depends(get_app_service),
) -> list[DocumentListResponse]:
    """列出知识库中的所有文档。

    Args:
        body: 文档列表请求体，含 kb_id

    Returns:
        list[DocumentListResponse]: 文档列表
    """
    docs = await svc.get_documents(body.kb_id)
    logger.info("Documents list: kb_id={} count={}", body.kb_id, len(docs))

    result = []
    for d in docs:
        eval_score = None
        eval_passed = None
        eval_detail = None
        meta_raw = d.get("meta_info")
        if meta_raw:
            try:
                if isinstance(meta_raw, str):
                    meta = json.loads(meta_raw)
                else:
                    meta = meta_raw
                eval_data = meta.get("eval", {}) if isinstance(meta, dict) else {}
                if eval_data:
                    eval_score = eval_data.get("overall_score")
                    eval_passed = eval_data.get("passed")
                    eval_detail = eval_data
            except (json.JSONDecodeError, AttributeError):
                pass
        result.append(
            DocumentListResponse(
                id=d["id"],
                filename=d["filename"],
                file_type=d["file_type"],
                file_size=d["file_size"],
                status=d["status"],
                created_at=d["created_at"].isoformat()
                if hasattr(d["created_at"], "isoformat")
                else d["created_at"],
                chunk_count=d.get("chunk_count", 0),
                eval_score=eval_score,
                eval_passed=eval_passed,
                eval_detail=eval_detail,
            )
        )
    return result


@router.post("/kbs/documents/upload", status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form(...),
    request: Request = None,
    svc: AppService = Depends(get_app_service),
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
    if len(contents) > MAX_FILE_SIZE:
        logger.warning(
            "Upload rejected (too large): filename={} size={} max={}",
            file.filename,
            len(contents),
            MAX_FILE_SIZE,
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
        raise BusinessError(
            Code.FILE_TYPE_UNSUPPORTED, Code.FILE_TYPE_UNSUPPORTED_MSG, 400
        )

    # MD5 去重：相同 KB 内不允许重复文件
    file_hash = hashlib.md5(contents).hexdigest()
    docs = await svc.db.get_documents(kb_id)
    for d in docs:
        if d.get("hash") == file_hash:
            logger.info(
                "Duplicate document detected: {} (hash={})", file.filename, file_hash
            )
            # 去重时保留评估数据
            if d.get("meta_info") and isinstance(d["meta_info"], str):
                try:
                    meta = json.loads(d["meta_info"])
                    if "eval" in meta:
                        await svc.db.update_document_meta_info(
                            d["id"], {"eval": meta["eval"]}
                        )
                except (json.JSONDecodeError, Exception):
                    pass
            return UploadDocumentResponse(
                doc_id=d["id"],
                status=d["status"],
                filename=d["filename"],
                dedup=True,
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
        svc.document.process_document(kb_id, doc_id, minio_key, file.filename, ext)
    )

    logger.info(
        "Upload success: doc_id={} filename={} kb_id={} size={}",
        doc_id,
        file.filename,
        kb_id,
        len(contents),
    )

    return UploadDocumentResponse(
        doc_id=doc_id, status="processing", filename=file.filename
    )


@router.post("/kbs/documents/status")
async def get_document_status(
    body: DocumentStatusRequest,
    svc: AppService = Depends(get_app_service),
) -> DocumentStatusResponse:
    """获取文档的处理状态。

    Args:
        body: 文档状态请求体，含 kb_id 和 doc_id

    Returns:
        DocumentStatusResponse: 含 status、chunk_count、progress、error         以及处理阶段详情
    """
    docs = await svc.db.get_documents(body.kb_id)
    doc = next((d for d in docs if d["id"] == body.doc_id), None)
    if not doc:
        return DocumentStatusResponse(status="not_found")
    return DocumentStatusResponse(
        status=doc["status"],
        chunk_count=doc.get("chunk_count"),
        progress=doc.get("processing_progress"),
        error=doc.get("error_msg"),
        processing_state=doc.get("processing_state"),
        processing_progress=doc.get("processing_progress"),
        processing_message=doc.get("processing_message"),
    )


@router.post("/kbs/documents/chunks")
async def get_document_chunks(
    body: DocumentChunksRequest,
    svc: AppService = Depends(get_app_service),
) -> ChunksResponse:
    """分页预览已处理文档的分块内容。

    Args:
        body: 分块预览请求体，含 kb_id、doc_id、page、page_size

    Returns:
        ChunksResponse: 含 items（当前页分块列表）、total（总量）、page、page_size
    """
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
            content=c["content"][: MAX_TABLE_TOKENS * 2],
            page=c.get("metadata", {}).get("page", 1),
            tokens=c.get("metadata", {}).get("tokens", 0),
            char_count=len(c["content"]),
            block_type=c.get("metadata", {}).get("block_type", "text"),
            parent_content=c.get("metadata", {}).get("parent_content"),
        )
        for c in result["items"]
    ]

    # 去重 parent_content：相同内容只传一次，其余用 parent_key 引用
    parent_map = {}
    parent_keys = {}  # parent_content → parent_key
    key_counter = 0
    for item in items:
        if item.parent_content:
            if item.parent_content not in parent_keys:
                key = f"p{key_counter}"
                key_counter += 1
                parent_keys[item.parent_content] = key
                parent_map[key] = item.parent_content
            item.parent_key = parent_keys[item.parent_content]
            item.parent_content = None  # 从 items 中移除，只放 parent_map

    return ChunksResponse(
        items=items,
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
        parent_map=parent_map,
    )


@router.post("/kbs/documents/delete")
async def delete_document(
    body: DocumentDeleteRequest,
    svc: AppService = Depends(get_app_service),
) -> DocumentDeleteResponse:
    """软删除文档（标记为 deleted），同时删除向量库中的分块。

    Args:
        body: 文档删除请求体，含 kb_id 和 doc_id

    Returns:
        DocumentDeleteResponse: 含 success 布尔值
    """
    ok = await svc.db.soft_delete_document(body.doc_id)
    if ok:
        svc.vector_store.delete_document(body.kb_id, body.doc_id)
        logger.info("Document deleted: kb_id={} doc_id={}", body.kb_id, body.doc_id)
    return DocumentDeleteResponse(success=ok)
