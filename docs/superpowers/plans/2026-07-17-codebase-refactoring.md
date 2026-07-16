# Codebase Refactoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `src/` 从平铺大文件重构为 `api → services → infra` 三层结构，清理死代码，重组测试目录，追加 CLAUDE.md 架构规约。

**Architecture:** 路由层（api/）纯转发 → 编排层（services/）组合子 service → 基础设施层（infra/）封装 DB/Redis/ChromaDB。rag/（RAG 流水线）和 chat/（对话管理）作为独立领域模块与 services/ 平级。

**Tech Stack:** Python 3.11+ / FastAPI / loguru / redis

## Global Constraints

- 不改动外部 API 行为和数据库结构
- 不改动前端代码
- 不引入新功能
- 不改变异步/同步模式（ChatManager 双版本共存）
- 每步完成后运行 `pytest tests/ -v && ruff check .` 验证通过再继续
- 删除 `old/`、`src/api/routes/`、`src/document_loader.py` 三处死代码

---

## 文件结构总览

### 创建的新文件
| 文件 | 职责 |
|------|------|
| `src/infra/redis_client.py` | 共享 Redis 客户端工厂 |
| `src/api/sse_utils.py` | 6 个 SSE 格式化纯函数 |
| `src/services/__init__.py` | 导出 AppService |
| `src/services/kb_service.py` | KBService（知识库 CRUD） |
| `src/services/document_service.py` | DocumentService（文档处理流水线） |
| `src/services/chat_service.py` | ChatService（对话问答） |
| `src/services/app_service.py` | AppService（编排入口，新） |
| `src/rag/__init__.py` | 导出 RAGChain, RAGContext |
| `src/rag/retrieval.py` | 检索 + 查询改写（纯函数） |
| `src/rag/prompt.py` | Prompt 构建（纯函数） |
| `src/rag/stream.py` | 流式生成 + Token 估算（纯函数） |
| `src/rag/chain.py` | RAGChain 主类 + RAGContext |
| `src/chat/__init__.py` | 导出 ChatManager |
| `src/chat/persistence.py` | PersistenceService（MySQL 持久化） |
| `src/chat/manager.py` | ChatManager（Redis/InMemory 会话 CRUD） |

### 修改的文件
| 文件 | 改动 |
|------|------|
| `claude.md` | 追加架构规约和自检清单 |
| `middleware/auth.py` | 从 AppService → infra/redis_client |
| `src/api/chat.py` | SSE 函数移到 sse_utils.py + import 更新 |
| `src/api/documents.py` | `_process_document_task` / `_enrich_chunk_pages` 下沉 |
| `src/api/*.py` (6 文件) | import 路径更新 |
| `src/cli/eval_ragas.py` | 4 处 import 更新 |

### 删除的文件
| 文件 | 原因 |
|------|------|
| `old/` | 历史快照，被当前代码全面超越 |
| `src/api/routes/` | 空目录 |
| `src/document_loader.py` | 无人引用 |
| `src/app_service.py` | 内容移至 services/ 包 |
| `src/rag_chain.py` | 内容移至 rag/ 包 |
| `src/chat_manager.py` | 内容移至 chat/ 包 |

---

## Task 批处理与执行顺序

```
Batch 1 (并行):      Task 1 + Task 2
Batch 2 (并行):      Task 3 + Task 4 + Task 5 + Task 6 + Task 7
Batch 3 (串行):      Task 8 （需要所有新文件存在）
Batch 4 (串行):      Task 9 + Task 10 + Task 11
```

---

### Task 1: CLAUDE.md 追加架构规约

**Files:**
- Modify: `claude.md`（追加 2 个章节，更新 2 个章节）

**Interfaces:**
- Consumes: 无
- Produces: 更新后的 CLAUDE.md（后续任务从中读取目录结构规则）

- [ ] **Step 1: 追加 `## 代码目录结构` 章节**

在 `## 技术栈` 之后，`## 数据流` 之前插入：

```markdown
## 代码目录结构（修改代码前必读）

```
src/
├── api/              # 纯路由层：只做请求→调用 service→返回，不写业务逻辑
│   ├── sse_utils.py  # SSE 格式化函数（纯工具，仅依赖 json）
│   └── model/        # 请求/响应 Pydantic 模型
├── services/         # 业务服务层：文档/知识库/对话的业务逻辑
├── rag/              # RAG 流水线
│   ├── chain.py      # RAGChain 主类（编排检索→精排→生成）
│   ├── retrieval.py  # 检索 + 查询改写（纯函数）
│   ├── prompt.py     # Prompt 构建（纯函数）
│   └── stream.py     # 流式生成（纯函数）
├── chat/             # 对话管理
│   ├── manager.py    # ChatManager（Redis/InMemory 会话 CRUD）
│   └── persistence.py# MySQL 持久化
├── core/             # 基础设施核心
├── config/           # 配置与常量
├── eval/             # 评估
├── parsers/          # 文档解析器
├── middleware/       # 中间件
├── infra/            # 基础设施：db / llm / search / chunking / auth
└── models.py         # 模型工厂

tests/
├── api/              # API 路由测试
├── services/         # 服务层测试
├── rag/              # RAG 模块测试
├── chat/             # 对话管理测试
├── parsers/          # 解析器测试
├── infra/            # 基础设施测试
├── config/           # 配置测试
├── middleware/        # 中间件测试
└── eval/             # 评估测试
```

### 层间调用规则
- ❌ `api/` 不得直接调用 `infra/` 或 `config/`（必须通过 `services/`）
- ❌ `api/chat.py` 不包含 SSE 格式化函数（在 `api/sse_utils.py`）
- ✅ `services/` 可调用 `infra/`、`rag/`、`chat/`
- ✅ `rag/chain.py` 编排 retrieval / prompt / stream，不包含实现细节
- ✅ `chat/manager.py` 不包含 MySQL 持久化逻辑（在 `chat/persistence.py`）

### 文件大小红线
- 单文件超过 400 行 → 必须拆分为模块包
- 单函数超过 80 行 → 必须拆分子函数
```

- [ ] **Step 2: 更新 `## 数据流` 章节**

替换为：
```markdown
## 数据流
文档上传 → parsers/router 解析 → infra/chunking 分块 → infra/db/vector_store 入库
用户提问 → rag/chain 检索/重排序/生成 → api SSE 推送前端
session/消息 → chat/manager(Redis) 写 + chat/persistence(MySQL) 落盘 → api/sessions 读
```

- [ ] **Step 3: 更新 `## 规则` 章节**

删除 `- \`old/\` 是历史快照，不改也不引用` 这一行。

- [ ] **Step 4: 更新 `## 验证` 章节，追加 LLM 自检清单**

在现有验证清单末尾追加：
```markdown
5. **代码位置检查**：新增/修改的代码放在正确的目录了吗？
6. **层次检查**：api/ 里是否只做了参数校验和路由转发，没有写业务逻辑？
7. **import 检查**：有没有违反层间调用规则的 import（如 api/ 里 import infra/）？
8. **文件大小检查**：单文件是否超过 400 行？是否需要拆分？
9. **测试对应检查**：如果增加了新模块，是否更新了 tests/ 对应目录的测试？
```

- [ ] **Step 5: 运行验证**

```bash
git diff claude.md
# 确认章节位置正确、无格式错误
```

---

### Task 2: 死代码清理

**Files:**
- Delete: `old/`（整个目录）
- Delete: `src/api/routes/`（空目录）
- Delete: `src/document_loader.py`

**Interfaces:**
- Consumes: 无
- Produces: 清理后的文件系统

- [ ] **Step 1: 删除 old/ 目录**

```bash
rm -rf old/
```

- [ ] **Step 2: 删除空目录 api/routes/**

```bash
rmdir src/api/routes/
```

- [ ] **Step 3: 删除 document_loader.py**

```bash
rm src/document_loader.py
```

验证以上文件不再存在：
```bash
ls old/ 2>&1 && echo "still exists" || echo "deleted OK"
ls src/api/routes/ 2>&1 && echo "still exists" || echo "deleted OK"
ls src/document_loader.py 2>&1 && echo "still exists" || echo "deleted OK"
```

- [ ] **Step 4: 运行 pytest 确认不影响测试**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -5
```
预期：已有测试全部通过（被删文件无人引用）。

---

### Task 3: infra/redis_client.py 独立

**Files:**
- Create: `src/infra/redis_client.py`
- Modify: `middleware/auth.py`（替换 AppService 依赖）
- Modify: `src/app_service.py`（删除 redis_client property）

**Interfaces:**
- Consumes: `src/config/settings.py` 中的 `REDIS_URL` 常量
- Produces: `get_redis_client()` 函数（返回 `redis.asyncio.Redis` 实例）

- [ ] **Step 1: 创建 src/infra/redis_client.py**

```python
"""共享 Redis 客户端工厂 — 提供统一的 Redis 连接创建入口。

所有需要 Redis 连接的模块（middleware、ChatManager 等）都应从此模块获取客户端，
避免各自创建连接实例。
"""

import redis.asyncio as redis_async

from src.config import REDIS_URL


_client: redis_async.Redis | None = None


def get_redis_client() -> redis_async.Redis:
    """获取 Redis 异步客户端单例。

    首次调用时创建连接，后续复用同一实例。
    使用延迟初始化，避免导入阶段产生网络连接。

    Returns:
        redis.asyncio.Redis 客户端实例
    """
    global _client
    if _client is None:
        _client = redis_async.from_url(REDIS_URL, decode_responses=True)
    return _client
```

- [ ] **Step 2: 修改 middleware/auth.py**

替换：
```python
from src.app_service import AppService

_service: AppService | None = None

def _get_service() -> AppService:
    global _service
    if _service is None:
        _service = AppService()
    return _service
```
为：
```python
from src.infra.redis_client import get_redis_client
```

替换两处 `_get_service().redis_client` 为 `get_redis_client()`：
```python
# 第 51 行
uid = await UserAuth.get_user_id_from_token_async(
    get_redis_client(), token
)
```
```python
# 第 70-71 行
uid = await UserAuth.get_user_id_from_token_async(
    get_redis_client(), token
)
```

- [ ] **Step 3: 修改 src/app_service.py — 删除 redis_client**

删除 `__init__` 中的 `self._redis = redis_async.from_url(REDIS_URL)` 和整个 `redis_client` property。

删除 import 行：`import redis.asyncio as redis_async` 和 `from src.config import REDIS_URL`（如果 REDIS_URL 只用于此处）。

删除 `AppService.__init__` 中的：
```python
self._redis = redis_async.from_url(REDIS_URL)
```

删除整个 property：
```python
@property
def redis_client(self):
    """获取 Redis 客户端实例。"""
    return self._redis
```

- [ ] **Step 4: 清理不再使用的 import**

`src/app_service.py` 中如果 `REDIS_URL` 和 `redis_async` 不再被其他代码使用，删除：
```python
import redis.asyncio as redis_async
# 和
from src.config import REDIS_URL
```

- [ ] **Step 5: 运行验证**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -5
ruff check src/infra/redis_client.py src/middleware/auth.py src/app_service.py
```

---

### Task 4: api/sse_utils.py SSE 函数独立

**Files:**
- Create: `src/api/sse_utils.py`
- Modify: `src/api/chat.py`（删除 SSE 函数 + 更新 import）

**Interfaces:**
- Produces: 6 个 SSE 格式化函数，签名与当前完全一致

- [ ] **Step 1: 创建 src/api/sse_utils.py**

```python
"""SSE (Server-Sent Events) 格式化工具函数。

提供统一的 SSE 事件文本构建函数，供流式聊天端点使用。
所有函数仅依赖标准库 json，无业务依赖。
"""

import json


def sse_status(stage: str, message: str, detail: str | None = None) -> str:
    """构建 SSE status 事件。

    Args:
        stage: 阶段标识（retrieving / reranking / generating）
        message: 阶段描述文本
        detail: 可选详细说明

    Returns:
        SSE 格式的文本行
    """
    data: dict[str, str] = {"stage": stage, "message": message}
    if detail:
        data["detail"] = detail
    return f"event: status\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_token(token: str) -> str:
    """构建 SSE token 事件。

    Args:
        token: LLM 生成的文本片段

    Returns:
        SSE 格式的文本行
    """
    return f"event: token\ndata: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"


def sse_citation(
    source: str,
    page: int,
    snippet: str,
    score: float = 0.0,
    highlighted_snippet: str | None = None,
) -> str:
    """构建 SSE citation 事件。

    Args:
        source: 文档来源名称
        page: 页码
        snippet: 内容摘要
        score: Reranker 分数
        highlighted_snippet: 高亮 HTML 片段

    Returns:
        SSE 格式的文本行
    """
    data = {
        "source": source,
        "page": page,
        "snippet": snippet,
        "score": score,
        "highlighted_snippet": highlighted_snippet,
    }
    return f"event: citation\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_done() -> str:
    """构建 SSE done 事件（标记流式响应结束）。"""
    return "event: done\ndata: {}\n\n"


def sse_error(error: str) -> str:
    """构建 SSE error 事件。

    Args:
        error: 错误描述文本
    """
    return f"event: error\ndata: {json.dumps({'error': error}, ensure_ascii=False)}\n\n"
```

- [ ] **Step 2: 修改 src/api/chat.py**

删除 6 个 SSE 函数（`sse_status`、`sse_token`、`sse_citation`、`sse_done`、`sse_error`）。

在文件开头的 import 区域追加：
```python
from src.api.sse_utils import sse_status, sse_token, sse_citation, sse_done, sse_error
```

- [ ] **Step 3: 运行验证**

```bash
ruff check src/api/sse_utils.py src/api/chat.py
pytest tests/api/test_chat.py -v --tb=short 2>&1 | tail -10
```

---

### Task 5: services/ 包 — 业务服务层

**Files:**
- Create: `src/services/__init__.py`
- Create: `src/services/kb_service.py`
- Create: `src/services/document_service.py`
- Create: `src/services/chat_service.py`
- Modify: `src/app_service.py`（读取源码，从中提取内容后重写为 `src/services/app_service.py`）
- Create: `src/services/app_service.py`（新编排入口）

**Interfaces:**
- Consumes: `src/infra/db/mysql_db.py` 中的 `MySQLDB`，`src/infra/db/vector_store.py` 中的 `VectorStore`，`src/parsers/router.py` 中的 `DocRouter`，`src/rag/chain.py` 中的 `RAGChain`（Task 6 产出）
- Produces: `KBService`、`DocumentService`、`ChatService`、`AppService`（编排入口）

- [ ] **Step 1: 创建 src/services/__init__.py**

```python
"""业务服务层 — 封装面向 UI 的业务操作和流程编排。

包含知识库管理、文档处理和 RAG 问答的业务逻辑。
"""

from src.services.app_service import AppService

__all__ = ["AppService"]
```

- [ ] **Step 2: 创建 src/services/kb_service.py**

从 `src/app_service.py` 提取知识库 CRUD 方法：

```python
"""知识库管理服务 — KB 的创建、查询、删除。"""

from src.infra.db.mysql_db import MySQLDB


class KBService:
    """知识库 CRUD 操作。

    Attributes:
        db: MySQLDB 实例，所有知识库元数据操作委托给它
    """

    def __init__(self, db: MySQLDB) -> None:
        self.db = db

    async def list_knowledge_bases(self, user_id: str = "") -> list[dict]:
        """列出所有知识库（含文档计数）。"""
        return await self.db.get_all_kb(user_id)

    async def create_knowledge_base(
        self,
        name: str,
        description: str = "",
        user_id: str = "",
    ) -> tuple[str, bool]:
        """创建知识库，已存在则直接返回。

        Returns:
            (kb_id, is_new) 元组
        """
        return await self.db.get_or_create_kb(user_id, name, description)

    async def soft_delete_documents_by_kb(self, kb_id: str) -> None:
        """软删除知识库下所有文档。"""
        await self.db.soft_delete_documents_by_kb(kb_id)

    async def soft_delete(self, kb_id: str) -> bool:
        """软删除知识库。"""
        return await self.db.soft_delete_kb(kb_id)
```

- [ ] **Step 3: 创建 src/services/document_service.py**

从 `src/app_service.py` 提取文档操作方法 + 从 `src/api/documents.py` 提取 `_process_document_task` 和 `_enrich_chunk_pages`：

```python
"""文档处理服务 — 文档的查询、删除、上传处理流水线。"""

import asyncio
import os
import uuid
from typing import Optional

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
```

- [ ] **Step 4: 创建 src/services/chat_service.py**

```python
"""对话问答服务 — 执行 RAG 问答并保存历史。"""

from src.rag.chain import RAGChain, RAGContext


class ChatService:
    """RAG 问答服务。

    封装 RAG 问答流程和对话历史保存。
    """

    def __init__(self, rag_chain: RAGChain) -> None:
        self.rag_chain = rag_chain

    def chat(
        self,
        kb_id: str,
        session_id: str,
        query: str,
    ) -> tuple[str, list[RAGContext]]:
        """执行一轮 RAG 问答。

        Returns:
            (answer_text, citations_list) 元组
        """
        token_gen, citations = self.rag_chain.chat_with_citations(
            kb_id, session_id, query,
        )
        full_answer = "".join([t for t in token_gen])
        sources = [f"{c.source} (第{c.page}页)" for c in citations]
        self.rag_chain.chat_manager.add_message(
            session_id, "assistant", full_answer, sources=sources,
        )
        return full_answer, citations
```

- [ ] **Step 5: 创建 src/services/app_service.py**

```python
"""应用业务逻辑编排入口。

组合 KBService、DocumentService、ChatService 三个子 service，
对外提供统一的业务接口。
"""

from typing import Optional

from src.infra.db.mysql_db import MySQLDB
from src.infra.db.vector_store import VectorStore
from src.parsers.router import DocRouter
from src.rag.chain import RAGChain, RAGContext
from src.services.kb_service import KBService
from src.services.document_service import DocumentService
from src.services.chat_service import ChatService


class AppService:
    """UI 与后端之间的业务逻辑编排层。

    持有 KBService / DocumentService / ChatService 三个子 service，
    编排跨子 service 的多步骤操作。
    """

    def __init__(
        self,
        mysql_db: Optional[MySQLDB] = None,
        vector_store: Optional[VectorStore] = None,
        router: Optional[DocRouter] = None,
        rag_chain: Optional[RAGChain] = None,
    ) -> None:
        self.db = mysql_db or MySQLDB()
        self.vector_store = vector_store or VectorStore()
        self.router = router or DocRouter()
        self.rag_chain = rag_chain or RAGChain()

        self.kb = KBService(self.db)
        self.document = DocumentService(self.db, self.vector_store, self.router)
        self.chat = ChatService(self.rag_chain)

    # ==================== 知识库 ====================

    async def list_knowledge_bases(self, user_id: str = "") -> list[dict]:
        return await self.kb.list_knowledge_bases(user_id)

    async def create_knowledge_base(
        self, name: str, description: str = "", user_id: str = "",
    ) -> tuple[str, bool]:
        return await self.kb.create_knowledge_base(name, description, user_id)

    async def delete_knowledge_base(self, kb_id: str) -> tuple[bool, str]:
        """删除知识库：软删文档 → 删 ChromaDB 集合 → 软删 KB。"""
        await self.kb.soft_delete_documents_by_kb(kb_id)
        try:
            await asyncio.to_thread(self.vector_store.delete_collection, kb_id)
            logger.info("ChromaDB delete_collection: kb_id={}", kb_id)
        except Exception:
            logger.warning("ChromaDB delete collection failed for kb={}", kb_id)
        ok = await self.kb.soft_delete(kb_id)
        if ok:
            logger.info("Knowledge base soft-deleted: {}", kb_id)
            return True, "知识库已删除"
        logger.warning("Knowledge base '{}' not found for deletion", kb_id)
        return False, "知识库不存在"

    # ==================== 文档 ====================

    async def get_documents(self, kb_id: str) -> list[dict]:
        return await self.document.get_documents(kb_id)

    async def delete_document(
        self, kb_id: str, doc_id: str, user_id: str,
    ) -> dict:
        return await self.document.delete_document(kb_id, doc_id, user_id)

    def upload_and_process(
        self, kb_id: str, file_path: str, filename: str,
    ) -> dict:
        return self.document.upload_and_process(kb_id, file_path, filename)

    # ==================== 问答 ====================

    def chat(
        self, kb_id: str, session_id: str, query: str,
    ) -> tuple[str, list[RAGContext]]:
        return self.chat.chat(kb_id, session_id, query)
```

需要补一个 import：在文件顶部加 `import asyncio` 和 `from loguru import logger`。

- [ ] **Step 6: 运行验证**

```bash
ruff check src/services/
python -c "from src.services import AppService; print('OK')"
```

---

### Task 6: rag/ 包 — RAG 流水线拆分

**Files:**
- Create: `src/rag/__init__.py`
- Create: `src/rag/retrieval.py`
- Create: `src/rag/prompt.py`
- Create: `src/rag/stream.py`
- Create: `src/rag/chain.py`

**Interfaces:**
- Produces: `RAGChain`（编排类）、`RAGContext`（数据类）
- Produces: 供 chain.py 调用的纯函数模块

- [ ] **Step 1: 创建 src/rag/__init__.py**

```python
"""RAG 问答流水线 — 检索、重排序、Prompt 构建、流式生成。"""

from src.rag.chain import RAGChain, RAGContext

__all__ = ["RAGChain", "RAGContext"]
```

- [ ] **Step 2: 创建 src/rag/retrieval.py**

从 `src/rag_chain.py` 提取检索和查询改写函数。将这些方法改为纯函数（`self` 参数变为显式传依赖）：

```python
"""检索与查询改写 — 向量检索、Reranker 精排、查询分类与改写。"""

import asyncio
from typing import Optional

from loguru import logger

from src.config import TOP_K_RETRIEVAL, TOP_K_RERANK, HYBRID_SEARCH_ENABLED
from src.infra.search.bm25_index import BM25Index, rrf_fusion
from src.infra.db.vector_store import VectorStore
from src.models import with_retry
from src.config import (
    RETRY_MAX_ATTEMPTS,
    RETRY_INITIAL_INTERVAL,
    RETRY_BACKOFF_FACTOR,
)
from src.rag.chain import RAGContext


async def search(
    query: str,
    kb_id: str,
    vector_store: VectorStore,
    bm25: Optional[BM25Index] = None,
) -> list[dict]:
    """执行语义检索（混合模式可选）。"""
    if HYBRID_SEARCH_ENABLED and bm25 and kb_id:
        dense_t = asyncio.to_thread(
            vector_store.similarity_search, kb_id, query, TOP_K_RETRIEVAL
        )
        bm25_t = asyncio.to_thread(bm25.search, kb_id, query, TOP_K_RETRIEVAL)
        d, b = await asyncio.gather(dense_t, bm25_t)
        results = rrf_fusion(d, b)
        logger.info(
            "RAG search: kb_id={} query_len={} results={}",
            kb_id, len(query), len(results),
        )
        return results

    if not kb_id:
        results = await asyncio.to_thread(
            vector_store.similarity_search_all, query, k=TOP_K_RETRIEVAL
        )
    else:
        results = await asyncio.to_thread(
            vector_store.similarity_search, kb_id, query, k=TOP_K_RETRIEVAL
        )
    logger.info(
        "RAG search: kb_id={} query_len={} results={}",
        kb_id, len(query), len(results),
    )
    return results


def rerank_results(
    query: str,
    results: list[dict],
    reranker,
) -> list[RAGContext]:
    """Reranker 精排，返回 top-N 的 RAGContext 列表。"""
    if not results:
        return []

    docs = [r["content"] for r in results]
    try:
        reranked = with_retry(
            reranker.rerank,
            max_attempts=RETRY_MAX_ATTEMPTS,
            initial_interval=RETRY_INITIAL_INTERVAL,
            backoff=RETRY_BACKOFF_FACTOR,
        )(query, docs)
    except Exception as e:
        logger.warning(
            "Rerank failed after {} attempts (using raw order): {}",
            RETRY_MAX_ATTEMPTS, e,
        )
        reranked = [
            {"index": i, "relevance_score": r.get("distance", 0)}
            for i, r in enumerate(results)
        ]

    contexts = []
    for item in reranked[:TOP_K_RERANK]:
        idx = item["index"]
        r = results[idx]
        metadata = r.get("metadata", {})
        pc = metadata.get("parent_content")
        score = item.get("relevance_score", 0)
        contexts.append(
            RAGContext(
                content=pc if pc else r["content"],
                source=metadata.get("source", ""),
                page=metadata.get("page", 0),
                doc_id=metadata.get("doc_id", ""),
                chunk_id=r["id"],
                parent_content=pc,
                score=score,
            )
        )
    return contexts


# ═══════════════════ 查询改写 ═══════════════════


def classify_query(query: str) -> str:
    """对用户查询进行分类。"""
    cleaned = query.strip()
    if not cleaned:
        return "clear"
    if any(w in cleaned for w in ["对比", "比较", "差异", "versus", "vs"]):
        return "compound"
    if any(w in cleaned for w in ["分析", "解释", "说明", "为什么"]):
        return "colloquial"
    if len(cleaned) < 10:
        return "fuzzy_short"
    return "clear"


def expand_query(query: str, history: list[dict]) -> str:
    """对模糊短查询进行扩展。"""
    if not history:
        return query
    for msg in reversed(history):
        if msg.get("role") == "user" and msg["content"] != query:
            return f"{msg['content']} {query}"
    return query


def condense_query(query: str) -> str:
    """将口语化查询精简。"""
    condense_patterns = ["分析", "解释", "说明", "为什么"]
    cleaned = query
    for pat in condense_patterns:
        cleaned = cleaned.replace(pat, "").strip()
    return cleaned if cleaned else query


def decompose_query(query: str) -> list[str]:
    """将对比类查询分解为子查询。"""
    separators = ["对比", "比较", "差异", "versus", "vs", "和", "与"]
    parts = [query]
    for sep in separators:
        new_parts = []
        for p in parts:
            new_parts.extend(p.split(sep))
        parts = [p.strip() for p in new_parts if p.strip()]
    return [p for p in parts if p]


def rewrite_query(query: str, history: list[dict]) -> str | list[str]:
    """根据分类执行相应的改写策略。"""
    t = classify_query(query)
    if t == "clear":
        return query
    if t == "fuzzy_short":
        return expand_query(query, history)
    if t == "colloquial":
        return condense_query(query)
    if t == "compound":
        return decompose_query(query)
    return query
```

- [ ] **Step 3: 创建 src/rag/prompt.py**

```python
"""Prompt 构建 — 将上下文、历史和问题组装为 LLM 消息列表。"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.infra.llm.prompt_manager import PromptManager
from src.rag.chain import RAGContext


def format_context(contexts: list[RAGContext]) -> str:
    """将检索上下文格式化为参考文档字符串。"""
    blocks = []
    for i, ctx in enumerate(contexts):
        blocks.append(
            f"[{i + 1}] 来源: {ctx.source} (第{ctx.page}页)\n内容: {ctx.content}"
        )
    return "\n\n".join(blocks)


def build_prompt(
    query: str,
    context: str,
    history: list[dict],
    prompt_manager: PromptManager,
) -> list:
    """构建含系统指令和对话历史的完整 prompt。"""
    messages = [SystemMessage(content=prompt_manager.get_system_prompt())]
    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    user_content = prompt_manager.get_user_template(context=context, query=query)
    messages.append(HumanMessage(content=user_content))
    return messages


def build_simple_prompt(
    query: str,
    history: list[dict],
    prompt_manager: PromptManager,
) -> list:
    """构建无检索上下文的简洁 prompt。"""
    messages = [SystemMessage(content=prompt_manager.get_system_prompt())]
    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=query))
    return messages
```

- [ ] **Step 4: 创建 src/rag/stream.py**

```python
"""流式生成 — LLM 流式回答生成 + Token 估算。"""

import time
from typing import Generator, Optional

from loguru import logger

from src.config import RETRY_MAX_ATTEMPTS, RETRY_INITIAL_INTERVAL, RETRY_BACKOFF_FACTOR


def estimate_usage(messages: list, output: str) -> dict:
    """粗略估算 token 用量。"""
    input_text = " ".join(
        getattr(m, "content", "") for m in messages if hasattr(m, "content")
    )
    input_tokens = max(1, len(input_text) // 2)
    output_tokens = max(1, len(output) // 2)
    return {"input": input_tokens, "output": output_tokens, "unit": "TOKENS"}


def stream_answer(
    messages: list,
    llm,
    tracer,
    trace_id: Optional[str] = None,
) -> Generator[str, None, None]:
    """流式生成 LLM 回答，支持指数退避重试。"""
    gen_id = None
    messages_snapshot = [
        {"role": getattr(m, "type", "unknown"), "content": m.content}
        for m in messages
        if hasattr(m, "type") or hasattr(m, "content")
    ]
    gen_id = tracer.start_generation(
        trace_id,
        "llm_stream",
        input_data=messages_snapshot,
        model=getattr(llm, "model", None),
    )

    last_error: Optional[Exception] = None
    full_output = ""
    last_token_usage = {}
    _stream_start = time.monotonic()
    _first_token = True

    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            stream = llm.stream(messages)
            for chunk in stream:
                content = chunk.content if hasattr(chunk, "content") else str(chunk)
                if content:
                    if _first_token:
                        _first_token = False
                        latency = (time.monotonic() - _stream_start) * 1000
                        logger.info("RAG first_token_latency={:.0f}ms", latency)
                    full_output += content
                    yield content
                if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                    u = chunk.usage_metadata
                    last_token_usage = {
                        "prompt_tokens": u.get("input_tokens", 0),
                        "completion_tokens": u.get("output_tokens", 0),
                        "total_tokens": u.get("total_tokens", 0),
                    }
            if not last_token_usage:
                usage = estimate_usage(messages, full_output)
                last_token_usage = {
                    "prompt_tokens": usage.get("input", 0),
                    "completion_tokens": usage.get("output", 0),
                    "total_tokens": usage.get("input", 0) + usage.get("output", 0),
                }
            tracer.end_generation(
                gen_id, trace_id, output=full_output, usage=last_token_usage,
            )
            return
        except Exception as e:
            last_error = e
            if attempt < RETRY_MAX_ATTEMPTS:
                wait = RETRY_INITIAL_INTERVAL * (
                    RETRY_BACKOFF_FACTOR ** (attempt - 1)
                )
                logger.warning(
                    "LLM stream failed (attempt {}/{}): {}. Retrying in {:.1f}s...",
                    attempt, RETRY_MAX_ATTEMPTS, e, wait,
                )
                time.sleep(wait)

    logger.error("LLM stream failed after {} attempts", RETRY_MAX_ATTEMPTS)
    error_msg = f"生成回答失败: {last_error}"
    full_output = error_msg
    tracer.end_generation(gen_id, trace_id, output=error_msg)
    yield error_msg
```

- [ ] **Step 5: 创建 src/rag/chain.py**

包含 `RAGContext` 数据类和 `RAGChain` 主类。RAGChain 的 `chat_with_citations()` 拆分为 4 个私有子方法：

```python
"""RAG 问答链 — 编排检索→精排→Prompt构建→流式生成的完整流水线。"""

from dataclasses import dataclass
from typing import Generator, Optional

from loguru import logger
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.config import (
    TOP_K_RETRIEVAL, TOP_K_RERANK,
    RETRY_MAX_ATTEMPTS, RETRY_INITIAL_INTERVAL, RETRY_BACKOFF_FACTOR,
    HYBRID_SEARCH_ENABLED, BM25_INDEX_DIR,
)
from src.infra.llm.langfuse_tracing import LangfuseTracer
from src.infra.llm.prompt_manager import PromptManager
from src.infra.search.query_router import QueryRouter
from src.infra.search.bm25_index import BM25Index
from src.infra.db.vector_store import VectorStore
from src.infra.db.mysql_db import MySQLDB
from src.chat import ChatManager
from src.models import get_embeddings, get_llm, get_rerank
from src.rag.retrieval import search, rerank_results, rewrite_query
from src.rag.prompt import build_prompt, build_simple_prompt
from src.rag.stream import stream_answer, estimate_usage


@dataclass
class RAGContext:
    """单个检索上下文分块 — 包含原文内容和来源元数据。"""

    content: str
    source: str
    page: int
    doc_id: str
    chunk_id: str
    parent_content: str | None = None
    score: float = 0.0

    def to_citation(self) -> str:
        """格式化为 Markdown 引用块。"""
        snippet = self.content[:200].replace("\n", " ")
        return f"> **来源:** {self.source} (第{self.page}页)\n> {snippet}\n"


class RAGChain:
    """RAG 问答链 — 编排检索、重排序、prompt 构建和流式生成的完整流水线。"""

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        mysql_db: Optional[MySQLDB] = None,
        chat_manager: Optional[ChatManager] = None,
        llm=None,
        embeddings=None,
        reranker=None,
    ) -> None:
        self.vector_store = vector_store or VectorStore()
        self.db = mysql_db or MySQLDB()
        self.chat_manager = chat_manager or ChatManager()
        self._llm = llm
        self._embeddings = embeddings
        self._reranker = reranker
        self._tracer = LangfuseTracer()
        self._prompt_manager = PromptManager()
        self.router = QueryRouter()
        self.bm25 = (
            BM25Index(index_dir=BM25_INDEX_DIR) if HYBRID_SEARCH_ENABLED else None
        )

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    @property
    def embeddings(self):
        if self._embeddings is None:
            self._embeddings = get_embeddings()
        return self._embeddings

    @property
    def reranker(self):
        if self._reranker is None:
            self._reranker = get_rerank()
        return self._reranker

    @property
    def prompt_manager(self):
        return self._prompt_manager

    # ═══════════ chat_with_citations — 主入口 ═══════════

    def chat_with_citations(
        self, kb_id: str, session_id: str, query: str,
    ) -> tuple[Generator[str, None, None], list[RAGContext]]:
        """生成带引用来源的流式回答 — RAG 流水线主入口。"""
        trace_id = self._tracer.start_trace(
            "chat_with_citations",
            {"kb_id": kb_id, "session_id": session_id, "query": query},
            session_id=session_id,
        )
        route = self.router.route(query)
        history = self.chat_manager.get_window(session_id)

        # Simple route
        if route == "simple":
            return self._handle_simple_route(query, history, trace_id)

        # Vague / Complex route — 改写查询
        if route in ("vague", "complex"):
            query = self._rewrite_if_needed(query, history)

        # Short query guard
        SHORT_QUERY_THRESHOLD = 5
        if len(query.strip()) < SHORT_QUERY_THRESHOLD:
            return self._handle_short_query(trace_id)

        # 检索
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            results = loop.run_until_complete(
                search(query, kb_id, self.vector_store, self.bm25)
            )
            loop.close()
        except Exception as e:
            return self._handle_search_error(e, trace_id)

        if not results:
            return self._handle_no_results(trace_id)

        # Rerank → Prompt → Stream
        rag_contexts = rerank_results(query, results, self.reranker)
        history = self.chat_manager.get_window(session_id)
        token_generator = self.stream_answer(query, rag_contexts, history, trace_id)
        self.chat_manager.add_message(session_id, "user", query)
        return token_generator, rag_contexts

    # ═══════════ 子方法 ═══════════

    def _handle_simple_route(self, query, history, trace_id):
        """处理 simple 路由：无检索，直接 LLM 回答。"""
        logger.info("Route: simple — direct LLM answer (no RAG)")
        prompt = build_simple_prompt(query, history, self.prompt_manager)
        token_gen = stream_answer(prompt, self.llm, self._tracer, trace_id)
        self.chat_manager.add_message(session_id := "", "user", query)
        return token_gen, []

    def _handle_short_query(self, trace_id):
        """处理过短查询。"""
        logger.info("Query too short (< {} chars)", 5)
        citations: list[RAGContext] = []

        def _gen():
            yield '查询内容过短，请输入更具体的财务问题（如"2024年营业收入是多少？"）'

        self._tracer.end_trace(trace_id, output="查询内容过短")
        return _gen(), citations

    def _handle_search_error(self, error: Exception, trace_id):
        """处理检索失败。"""
        error_msg = str(error)
        logger.exception("Vector search failed: {}", error_msg)
        citations: list[RAGContext] = []

        def _gen():
            yield f"检索失败: {error_msg}"

        return _gen(), citations

    def _handle_no_results(self, trace_id):
        """处理检索结果为空。"""
        logger.info("No results found")
        citations: list[RAGContext] = []

        def _gen():
            yield "未在文档中找到相关数据。"

        return _gen(), citations

    def _rewrite_if_needed(self, query: str, history: list) -> str:
        """根据需要执行查询改写。"""
        rewritten = rewrite_query(query, history)
        if isinstance(rewritten, list):
            rewritten = " ".join(rewritten)
        return rewritten

    # ═══════════ 公共方法 ═══════════

    def stream_answer(self, query, contexts, history, trace_id=None):
        """构建 prompt 并流式生成回答。"""
        from src.rag.prompt import format_context
        context_str = format_context(contexts)
        prompt = build_prompt(query, context_str, history, self.prompt_manager)
        return stream_answer(prompt, self.llm, self._tracer, trace_id)
```

- [ ] **Step 6: 运行验证**

```bash
ruff check src/rag/
python -c "from src.rag import RAGChain, RAGContext; print('OK')"
```

---

### Task 7: chat/ 包 — 对话管理拆分

**Files:**
- Create: `src/chat/__init__.py`
- Create: `src/chat/persistence.py`
- Create: `src/chat/manager.py`（从 `src/chat_manager.py` 提取核心 + 剥离持久化逻辑）

**Interfaces:**
- Produces: `ChatManager`（Redis/InMemory 会话管理）
- Produces: `PersistenceService`（MySQL 持久化）

- [ ] **Step 1: 创建 src/chat/__init__.py**

```python
"""对话管理 — 会话历史缓存（Redis/InMemory）和 MySQL 持久化。"""

from src.chat.manager import ChatManager

__all__ = ["ChatManager"]
```

- [ ] **Step 2: 创建 src/chat/persistence.py**

```python
"""对话历史持久化 — MySQL 异步写入。"""

from typing import Optional

from loguru import logger

from src.infra.db.mysql_db import MySQLDB


class PersistenceService:
    """对话历史 MySQL 持久化。

    负责将会话和消息异步写入 MySQL，失败只记日志不抛异常。
    """

    def __init__(self, mysql_db: MySQLDB) -> None:
        self._mysql_db = mysql_db

    async def save_session(
        self, session_id: str, title: str, kb_id: str,
    ) -> None:
        """异步创建会话记录。"""
        try:
            await self._mysql_db.create_session(session_id, title, kb_id)
        except Exception as e:
            logger.warning("Failed to save session async: {}", e)

    async def save_messages(
        self,
        session_id: str,
        kb_id: str,
        user_msg: str,
        assistant_msg: str,
        sources: Optional[list[str]] = None,
    ) -> None:
        """异步写入 user + assistant 消息。"""
        try:
            await self._mysql_db.save_message(
                session_id, kb_id, "user", user_msg, None,
            )
            await self._mysql_db.save_message(
                session_id, kb_id, "assistant", assistant_msg, sources,
            )
        except Exception as e:
            logger.warning("Failed to save messages async: {}", e)

    def cleanup_session(self, session_id: str) -> None:
        """清理会话相关数据（当前委托给 ChatManager 的 clear_history）。"""
        pass
```

- [ ] **Step 3: 创建 src/chat/manager.py**

从 `src/chat_manager.py` 提取，保留 Redis/InMemory 会话管理，MySQL 持久化通过 `PersistenceService`：

```python
"""对话历史管理器 — Redis 优先，InMemory 降级。"""

import json
from typing import Optional

import redis.asyncio as redis_async
import redis as redis_sync
from loguru import logger

from src.config import MEMORY_WINDOW, REDIS_URL, REDIS_TTL
from src.chat.persistence import PersistenceService
from src.infra.db.mysql_db import MySQLDB


class ChatManager:
    """对话历史管理器 — Redis 优先，内存降级。"""

    def __init__(
        self, redis_url: Optional[str] = None, ttl: int = REDIS_TTL,
    ) -> None:
        self.ttl = ttl
        self._redis_url = redis_url or REDIS_URL
        self._redis = None
        self._in_memory: bool = False
        self._memory_store: dict[str, list[dict]] = {}
        self._persistence: Optional[PersistenceService] = None
        self._init_redis(self._redis_url)

    def set_mysql_db(self, mysql_db: MySQLDB) -> None:
        """注入 MySQLDB 实例（包装为 PersistenceService）。"""
        self._persistence = PersistenceService(mysql_db)

    # ═══════════ 异步持久化（委托给 PersistenceService） ═══════════

    async def save_session_async(
        self, session_id: str, title: str, kb_id: str,
    ) -> None:
        if self._persistence:
            await self._persistence.save_session(session_id, title, kb_id)

    async def save_messages_async(
        self,
        session_id: str,
        kb_id: str,
        user_msg: str,
        assistant_msg: str,
        sources: Optional[list[str]] = None,
    ) -> None:
        if self._persistence:
            await self._persistence.save_messages(
                session_id, kb_id, user_msg, assistant_msg, sources,
            )

    def cleanup_session(self, session_id: str) -> None:
        self.clear_history(session_id)

    # ═══════════ Redis / InMemory 核心 ═══════════

    def _init_redis(self, redis_url: str) -> None:
        try:
            conn = redis_sync.from_url(redis_url, decode_responses=True)
            conn.ping()
            conn.close()
            self._redis = redis_async.from_url(redis_url, decode_responses=True)
            logger.info("ChatManager: Redis async client created at {}", redis_url)
        except Exception as e:
            self._redis = None
            self._in_memory = True
            logger.warning(
                "ChatManager: Redis unavailable ({}), using InMemory fallback", e,
            )

    def _get_sync_redis(self):
        return redis_sync.from_url(self._redis_url, decode_responses=True)

    def _ensure_redis(self) -> None:
        # 保持原实现不变...（省略，与旧版相同）
        if self._in_memory:
            try:
                conn = self._get_sync_redis()
                conn.ping()
                conn.close()
                self._in_memory = False
                logger.info("ChatManager: Redis reconnected")
            except Exception:
                self._in_memory = True
            return
        try:
            conn = self._get_sync_redis()
            conn.ping()
            conn.close()
        except Exception:
            logger.warning("ChatManager: Redis ping failed, attempting reconnect...")
            try:
                conn = self._get_sync_redis()
                conn.ping()
                conn.close()
                logger.info("ChatManager: Redis reconnected")
            except Exception as e:
                logger.warning(
                    "ChatManager: Redis reconnect failed, falling back to InMemory: {}",
                    e,
                )
                self._redis = None
                self._in_memory = True

    def _session_key(self, session_id: str) -> str:
        return f"chat_history:{session_id}"

    def get_history(self, session_id: str) -> list[dict]:
        self._ensure_redis()
        if self._in_memory:
            return list(self._memory_store.get(session_id, []))
        key = self._session_key(session_id)
        try:
            conn = self._get_sync_redis()
            raw = conn.lrange(key, 0, -1)
            conn.close()
            return [json.loads(m) for m in raw]
        except Exception as e:
            logger.warning("ChatManager: Redis get_history failed: {}", e)
            return []

    def add_message(
        self, session_id: str, role: str, content: str, sources=None,
        prompt_tokens=0, completion_tokens=0, total_tokens=0, model_name="",
    ) -> None:
        msg: dict = {"role": role, "content": content}
        if sources:
            msg["sources"] = sources
        if prompt_tokens or completion_tokens or total_tokens:
            msg.update({
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            })
        if model_name:
            msg["model_name"] = model_name

        self._ensure_redis()
        if self._in_memory:
            if session_id not in self._memory_store:
                self._memory_store[session_id] = []
            self._memory_store[session_id].append(msg)
            return
        key = self._session_key(session_id)
        try:
            conn = self._get_sync_redis()
            conn.rpush(key, json.dumps(msg, ensure_ascii=False))
            conn.expire(key, self.ttl)
            conn.close()
        except Exception as e:
            logger.warning("ChatManager: Redis add_message failed: {}", e)

    def get_window(
        self, session_id: str, window_size: int = MEMORY_WINDOW,
    ) -> list[dict]:
        history = self.get_history(session_id)
        return history[-window_size:] if len(history) > window_size else history

    def clear_history(self, session_id: str) -> None:
        self._ensure_redis()
        if self._in_memory:
            self._memory_store.pop(session_id, None)
            return
        key = self._session_key(session_id)
        try:
            conn = self._get_sync_redis()
            conn.delete(key)
            conn.close()
        except Exception as e:
            logger.warning("ChatManager: Redis clear_history failed: {}", e)

    # ═══════════ 异步方法 ═══════════

    async def _ensure_redis_async(self) -> None:
        # 保持原实现不变...
        if self._in_memory:
            try:
                c = redis_async.from_url(self._redis_url, decode_responses=True)
                await c.ping()
                self._redis = c
                self._in_memory = False
            except Exception:
                pass
            return
        try:
            await self._redis.ping()
        except Exception:
            self._redis = None
            self._in_memory = True

    async def add_message_async(
        self, session_id: str, role: str, content: str, **kwargs,
    ) -> None:
        await self._ensure_redis_async()
        if self._in_memory:
            self.add_message(session_id, role, content, **kwargs)
            return
        msg = {"role": role, "content": content}
        key = self._session_key(session_id)
        try:
            await self._redis.rpush(key, json.dumps(msg, ensure_ascii=False))
            await self._redis.expire(key, self.ttl)
        except Exception as e:
            logger.warning("add_message_async failed: {}", e)

    async def get_history_async(self, session_id: str) -> list[dict]:
        await self._ensure_redis_async()
        if self._in_memory:
            return list(self._memory_store.get(session_id, []))
        key = self._session_key(session_id)
        try:
            raw = await self._redis.lrange(key, 0, -1)
            return [json.loads(m) for m in raw]
        except Exception as e:
            logger.warning("get_history_async failed: {}", e)
            return []

    async def clear_history_async(self, session_id: str) -> None:
        await self._ensure_redis_async()
        if self._in_memory:
            self._memory_store.pop(session_id, None)
            return
        key = self._session_key(session_id)
        try:
            await self._redis.delete(key)
        except Exception as e:
            logger.warning("clear_history_async failed: {}", e)
```

- [ ] **Step 4: 运行验证**

```bash
ruff check src/chat/
python -c "from src.chat import ChatManager; print('OK')"
```

---

### Task 8: 调用方 import 路径更新

**Files:**
- Modify: `src/api/*.py`（7 files: auth.py, chat.py, documents.py, health.py, knowledge_base.py, kb_eval.py, sessions.py）
- Modify: `src/api/chat.py`（RAGChain import）
- Modify: `src/app_service.py`（rag + chat import）
- Modify: `src/cli/eval_ragas.py`（4 处 import）
- Modify: `tests/conftest.py`
- Modify: `tests/reset_data.py`

**Interfaces:**
- Consumes: Task 5-7 创建的新包
- Produces: 所有调用方指向新路径

- [ ] **Step 1: 更新 src/api/ 下的 7 个路由文件**

统一替换：
```python
# 改前
from src.app_service import AppService

# 改后
from src.services.app_service import AppService
```

涉及文件：`auth.py`、`chat.py`、`documents.py`、`health.py`、`knowledge_base.py`、`kb_eval.py`、`sessions.py`

- [ ] **Step 2: 更新 src/api/chat.py 的 RAGChain import**

```python
# 改前
from src.rag_chain import RAGChain

# 改后
from src.rag.chain import RAGChain
```

- [ ] **Step 3: 更新 src/app_service.py 的 rag + chat import**

```python
# 改前
from src.rag_chain import RAGChain, RAGContext
from src.chat_manager import ChatManager

# 改后
from src.rag import RAGChain, RAGContext
from src.chat import ChatManager
```

- [ ] **Step 4: 更新 cli/eval_ragas.py 的 4 处 import**

```python
# 改前
from src.rag_chain import RAGChain
from src.app_service import AppService

# 改后
from src.rag import RAGChain
from src.services import AppService
```

- [ ] **Step 5: 更新 tests/conftest.py 和 tests/reset_data.py**

```python
# 改前
from src.app_service import AppService

# 改后
from src.services import AppService
```

- [ ] **Step 6: 确认再无旧路径引用**

```bash
grep -rn "from src\.app_service\|from src\.rag_chain\|from src\.chat_manager" src/ tests/
```
预期：只出现 `src/services/app_service`、`src/rag/`、`src/chat/` 等新路径，旧路径全无。

---

### Task 9: 删除旧文件

**Files:**
- Delete: `src/app_service.py`
- Delete: `src/rag_chain.py`
- Delete: `src/chat_manager.py`

- [ ] **Step 1: 删除三个旧文件**

```bash
rm src/app_service.py src/rag_chain.py src/chat_manager.py
```

- [ ] **Step 2: 确认删除成功**

```bash
ls src/app_service.py src/rag_chain.py src/chat_manager.py 2>&1
```
预期：三个文件都不存在。

- [ ] **Step 3: 运行 pytest 确认旧文件删除后无影响**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -5
```

---

### Task 10: 测试目录重组

**Files:**
- 创建 12 个测试子目录
- 移动/拆分为 20+ 个测试文件
- 更新 conftest 和 reset_data 的 import

- [ ] **Step 1: 创建测试子目录**

```bash
mkdir -p tests/services tests/rag tests/chat tests/parsers \
  tests/infra/db tests/infra/search tests/infra/llm \
  tests/infra/chunking tests/infra/auth \
  tests/config tests/middleware tests/eval
```

- [ ] **Step 2: 拆分并移动 test_rag_chain.py**

此文件 28K 行，按 rag/ 模块拆为 4 个文件：

```bash
# 先复制原文件为 4 个副本
cp tests/test_rag_chain.py tests/rag/test_chain.py
cp tests/test_rag_chain.py tests/rag/test_retrieval.py
cp tests/test_rag_chain.py tests/rag/test_prompt.py
cp tests/test_rag_chain.py tests/rag/test_stream.py
```

然后在每个副本中删除不属于该模块的测试方法（按测试方法名筛选）：
- `tests/rag/test_chain.py`：保留 `test_chat_with_citations`、`test_simple_route` 等编排测试
- `tests/rag/test_retrieval.py`：保留 `test_search`、`test_rerank`、`test_classify_query` 等
- `tests/rag/test_prompt.py`：保留 `test_build_prompt`、`test_format_context` 等
- `tests/rag/test_stream.py`：保留 `test_stream_answer`、`test_estimate_usage` 等

更新每个文件的 import：
```python
# 改前
from src.rag_chain import RAGChain, RAGContext

# 改后
from src.rag import RAGChain, RAGContext
```

- [ ] **Step 3: 移动其他测试文件**

```bash
# test_rag_chain_tracing.py
cp tests/test_rag_chain_tracing.py tests/rag/
sed -i 's/from src.rag_chain import/from src.rag import/' tests/rag/test_rag_chain_tracing.py

# test_chat_manager.py
cp tests/test_chat_manager.py tests/chat/
sed -i 's/from src.chat_manager import/from src.chat.manager import/' tests/chat/test_chat_manager.py

# test_app_service.py
cp tests/test_app_service.py tests/services/
sed -i 's/from src.app_service import/from src.services.app_service import/' tests/services/test_app_service.py

# parser 测试
cp tests/test_base.py tests/parsers/
cp tests/test_docx_parser.py tests/parsers/
cp tests/test_pymupdf_parser.py tests/parsers/
cp tests/test_txt_parser.py tests/parsers/
cp tests/test_router.py tests/parsers/

# infra/db
cp tests/test_mysql_db.py tests/infra/db/
cp tests/test_vector_store.py tests/infra/db/
cp tests/test_file_store.py tests/infra/db/

# infra/search
cp tests/test_bm25_index.py tests/infra/search/
cp tests/test_query_router.py tests/infra/search/

# infra/llm
cp tests/test_langfuse.py tests/infra/llm/
cp tests/test_prompt_manager_fallback.py tests/infra/llm/

# infra/chunking
cp tests/test_chunking.py tests/infra/chunking/
cp tests/test_chunk_enhancer.py tests/infra/chunking/
cp tests/test_chunk_validator.py tests/infra/chunking/

# infra/auth
cp tests/test_auth.py tests/infra/auth/

# config
cp tests/test_settings.py tests/config/
cp tests/test_response_codes.py tests/config/

# middleware
cp tests/test_middleware.py tests/middleware/
cp tests/test_api_error.py tests/middleware/

# eval
cp tests/test_eval_ragas.py tests/eval/
cp tests/unit/test_chunk_scorer.py tests/eval/
```

- [ ] **Step 4: 更新 conftest.py 和 reset_data.py 的 import**

```python
# tests/conftest.py — 改前
from src.app_service import AppService
# 改后
from src.services.app_service import AppService

# tests/reset_data.py — 同上
```

- [ ] **Step 5: 确认所有新位置的文件跑通**

```bash
pytest tests/services/ tests/rag/ tests/chat/ tests/parsers/ \
  tests/infra/db/ tests/infra/search/ tests/infra/llm/ \
  tests/infra/chunking/ tests/infra/auth/ tests/config/ \
  tests/middleware/ tests/eval/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 6: 删除旧测试文件和空目录**

```bash
rm tests/unit/test_chunk_scorer.py
rmdir tests/unit/
```

确认旧测试文件都已迁移完成后再删除原文件：
```bash
rm tests/test_rag_chain.py tests/test_rag_chain_tracing.py \
  tests/test_chat_manager.py tests/test_app_service.py \
  tests/test_base.py tests/test_docx_parser.py tests/test_pymupdf_parser.py \
  tests/test_txt_parser.py tests/test_router.py \
  tests/test_mysql_db.py tests/test_vector_store.py tests/test_file_store.py \
  tests/test_bm25_index.py tests/test_query_router.py \
  tests/test_langfuse.py tests/test_prompt_manager_fallback.py \
  tests/test_chunking.py tests/test_chunk_enhancer.py tests/test_chunk_validator.py \
  tests/test_auth.py tests/test_settings.py tests/test_response_codes.py \
  tests/test_middleware.py tests/test_api_error.py tests/test_eval_ragas.py
```

---

### Task 11: 最终验证

**Files:**
- 所有修改的文件

- [ ] **Step 1: 运行全部测试**

```bash
pytest tests/ -v --tb=long 2>&1 | tail -30
```

- [ ] **Step 2: 运行代码格式检查**

```bash
ruff check . --fix
```

- [ ] **Step 3: 确认无残留调试代码**

```bash
grep -rn "print(" src/ --include="*.py" | grep -v "#"
grep -rn "TODO\|FIXME\|HACK\|XXX" src/ --include="*.py" | grep -v "TODO:" | head -5
```

- [ ] **Step 4: 确认 CLAUDE.md 无 old/ 引用**

```bash
grep "old/" claude.md
```
预期：无输出（已删除）。
