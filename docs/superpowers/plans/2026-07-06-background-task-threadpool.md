# Background Task Thread Pool & Concurrency Control

> **注意：本方案已被取代。** 实际落地经过：commit `32aa73c` 实现了此方案 → 随后在 async migration 方案中被 `git revert e987203` 回退 → 最终落地为全异步方案（见 `2026-07-06-async-migration.md`）。
> 
> 💡 保留此文档作为历史记录和思路对比参考。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the document background processing from pseudo-async (`async def` with all sync I/O) to true thread-backed processing with concurrency control.

**Architecture:** Only one task. Convert `_process_document()` to a plain synchronous function and launch it via `asyncio.to_thread()` so the event loop is never blocked. Add an `asyncio.Semaphore(3)` at module level to cap concurrent background tasks. No retry logic — that's in the requirements pool (E-09).

**Tech Stack:** Python 3.11+ / FastAPI / asyncio

## Global Constraints

- `_process_document()` body must stay functionally identical — only the function signature and how it's launched changes.
- The semaphore must be module-level (not per-request) so it caps total concurrent background work.
- No new dependencies.
- Existing tests must pass with no changes to test code.

---

### Task 1: Refactor `_process_document` to sync + thread pool

**Files:**
- Modify: `src/api/routes/documents.py` (full file, lines 1-271)
- No changes needed to any tests.

**Interfaces:**
- Consumes: `_process_document(svc: AppService, kb_id: str, doc_id: str, minio_key: str, filename: str, ext: str) -> None` — same signature except `async def` → `def`.
- Consumes: `asyncio.Semaphore(3)` — module-level `_process_semaphore`.
- Produces: Upload endpoint still returns HTTP 202 with the same response shape.
- Produces: Background task runs in a thread pool, event loop never blocked.

- [ ] **Step 1: Write the failing test**

Add a test file `tests/api/test_background_task.py` that verifies the background dispatch mechanism works:

```python
"""背景任务线程池的单元测试。"""

import asyncio
from unittest.mock import ANY, MagicMock, patch

import pytest

from src.api.routes.documents import _dispatch_processing


@pytest.mark.asyncio
async def test_dispatch_processing_acquires_semaphore():
    """验证 _dispatch_processing 正确获取信号量并调用 to_thread。"""
    mock_svc = MagicMock()
    mock_svc.db = MagicMock()

    with (
        patch("src.api.routes.documents._process_semaphore") as mock_sem,
        patch("src.api.routes.documents.asyncio.to_thread") as mock_to_thread,
    ):
        mock_sem.acquire = asyncio.Semaphore(1).acquire
        mock_to_thread.return_value = "done"

        # 让 to_thread 实际返回可 await 的对象
        async def fake_to_thread(func, *args):
            func(*args)
            return "done"

        mock_to_thread.side_effect = fake_to_thread

        await _dispatch_processing(mock_svc, "kb1", "d1", "key", "f.txt", ".txt")

        mock_to_thread.assert_called_once()
        args, _ = mock_to_thread.call_args
        func = args[0]
        # 验证 to_thread 调用的第一个参数是同步函数
        assert not asyncio.iscoroutinefunction(func)  # 不是 async def
```

Also update the existing test to verify the endpoint returns `202` and status `"processing"`:

```python
# Add to tests/api/test_documents.py
@patch("src.api.routes.documents._get_service")
def test_upload_document_returns_immediately(mock_get_service):
    """POST upload 返回 202，不等待后台处理完成。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_documents.return_value = []
    mock_svc.db.add_document.return_value = "test-doc-uuid"

    with patch("src.api.routes.documents._dispatch_processing") as mock_dispatch:
        response = client.post(
            "/api/kbs/kb-1/documents/upload",
            files={"file": ("test.pdf", b"%PDF-1.4 content", "application/pdf")},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "processing"
        assert data["doc_id"] is not None
        # 验证后台任务被启动
        mock_dispatch.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_background_task.py tests/api/test_documents.py::test_upload_document_returns_immediately -v`

Expected:
- `test_upload_document_returns_immediately` — FAIL with `_dispatch_processing not defined`
- Both tests currently have import issues

- [ ] **Step 3: Implement the changes in `documents.py`**

Three changes:

**Change 1 — Add semaphore constant and import after the existing imports (line 8-21):**

```python
import asyncio
import hashlib
import os
import tempfile
import uuid

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from loguru import logger

from src.app_service import AppService
from src.infra.chunk_router import ChunkRouter
from src.infra.chunk_validator import ChunkData, validate_chunks
from src.infra.file_store import FileStore

router = APIRouter()

# 后台文档处理并发上限
_process_semaphore = asyncio.Semaphore(3)
```

**Change 2 — Replace the `asyncio.create_task` line in `upload_document()` (line 137-140):**

Replace:

```python
    # 启动后台处理任务
    asyncio.create_task(
        _process_document(svc, kb_id, doc_id, minio_key, file.filename, ext)
    )
```

With:

```python
    # 启动后台处理任务（线程池中执行，不阻塞事件循环）
    asyncio.create_task(
        _dispatch_processing(svc, kb_id, doc_id, minio_key, file.filename, ext)
    )
```

**Change 3 — Add `_dispatch_processing` wrapper and convert `_process_document` (add after line 271, and modify `_process_document`):**

Convert `_process_document` from `async def` to `def`:

```python
def _process_document(
    svc: AppService, kb_id: str, doc_id: str, minio_key: str, filename: str, ext: str
) -> None:
    """在后台处理文档：从 MinIO 下载 → 解析 → 分块 → 入库。

    这是一个纯同步函数，在 asyncio.to_thread() 的线程池中执行，
    不会阻塞主事件循环。

    Args:
        svc: AppService 实例
        kb_id: 知识库 UUID
        doc_id: 文档 UUID
        minio_key: MinIO 存储路径
        filename: 文件名
        ext: 文件扩展名（含点号，如 .pdf）
    """
    # ... body stays exactly the same, just remove the "async" keyword ...
    tmp_path = None
    try:
        # 阶段 1：从 MinIO 下载
        svc.db.update_document_status(
            doc_id,
            "processing",
            processing_state="extracting",
            processing_progress=30,
            processing_message="正在从存储下载文件",
        )
        fs = FileStore()
        contents = fs.download(minio_key)
        if contents is None:
            raise RuntimeError(f"无法从 MinIO 下载文档: {minio_key}")

        svc.db.update_document_status(
            doc_id,
            "processing",
            processing_state="extracting",
            processing_progress=30,
            processing_message="正在解析文档",
        )
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        parse_result = svc.router.parse(tmp_path)

        if parse_result.is_scanned:
            error_msg = "文档为扫描件或无可提取文本，MVP 暂不支持 OCR"
            svc.db.update_document_status(doc_id, "failed", error_msg=error_msg)
            logger.warning("Scanned document detected: {}", filename)
            return

        # 阶段 2：策略感知分块
        svc.db.update_document_status(
            doc_id,
            "processing",
            processing_state="chunking",
            processing_progress=50,
            processing_message="正在检测分块策略",
        )
        full_text = "\n".join(c.content for c in parse_result.chunks)
        strategy = ChunkRouter.detect_strategy(full_text, parse_result.chunks)
        chunker = ChunkRouter.get_chunker(strategy)
        logger.info("Detected chunk strategy '{}' for document: {}", strategy, filename)

        svc.db.update_document_status(
            doc_id,
            "processing",
            processing_state="chunking",
            processing_progress=50,
            processing_message=f"正在按 {strategy} 策略分块",
        )
        chunks = chunker.chunk(full_text, {"source": filename, "doc_id": doc_id})

        # 分块质量校验
        chunk_data_list = [
            ChunkData(content=c["content"], metadata=c["metadata"]) for c in chunks
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

        # 阶段 3：写入向量库
        svc.db.update_document_status(
            doc_id,
            "processing",
            processing_state="indexing",
            processing_progress=70,
            processing_message="正在写入向量库",
        )
        count = svc.vector_store.add_chunks(kb_id, chunk_data_list, doc_id)

        # 标记完成
        svc.db.update_document_status(
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
        try:
            svc.db.update_document_status(doc_id, "failed", error_msg=error_msg)
        except Exception:
            logger.exception("Failed to update document status after processing error")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError as e:
                logger.warning("Failed to clean up temp file {}: {}", tmp_path, e)
```

Add the `_dispatch_processing` async wrapper function after `_process_document`:

```python
async def _dispatch_processing(
    svc: AppService, kb_id: str, doc_id: str, minio_key: str, filename: str, ext: str
) -> None:
    """在信号量控制下将文档处理派发到线程池执行。

    通过 asyncio.to_thread() 将同步的 _process_document 提交到
    默认线程池，避免阻塞主事件循环。_process_semaphore 限制
    同时处理的最大文档数。

    Args:
        svc: AppService 实例
        kb_id: 知识库 UUID
        doc_id: 文档 UUID
        minio_key: MinIO 存储路径
        filename: 文件名
        ext: 文件扩展名（含点号，如 .pdf）
    """
    async with _process_semaphore:
        await asyncio.to_thread(
            _process_document,
            svc, kb_id, doc_id, minio_key, filename, ext,
        )
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/api/test_background_task.py tests/api/test_documents.py -v`

Expected:
- `test_upload_document_returns_immediately` — PASS
- `test_dispatch_processing_acquires_semaphore` — PASS (or near-PASS, mock adjustments may be needed)
- Existing tests pass

Run: `ruff check src/api/routes/documents.py`

Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/documents.py tests/api/test_background_task.py tests/api/test_documents.py
git commit -m "fix(upload): run _process_document in thread pool with semaphore(3) concurrency limit"
```

---
