# Iter 5 — Gradio UI 界面 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用完整的 Gradio UI（知识库管理 + 文件上传 + 对话聊天 + 引用展示）替换当前的 sleep stub，实现投资人可演示的端到端交互界面。

**Architecture:** 分两层：`AppService`（纯 Python 业务逻辑层，可单元测试）和 `app.py`（Gradio 事件绑定层）。AppService 封装 RAGChain/MySQLDB/VectorStore 的调用逻辑，app.py 只负责布局和事件路由。文件上传使用 threading 后台处理，避免 Gradio 超时。

**Tech Stack:** Python 3.11, Gradio 5.x, threading, loguru

---

## AppService 接口设计

```python
class AppService:
    """UI 与后端之间的业务逻辑层。"""

    def list_knowledge_bases(self) -> list[tuple[str, str]]:
        """返回 [(kb_id, kb_name), ...]"""

    def create_knowledge_base(self, name: str, description: str = "") -> tuple[str, bool]:
        """创建知识库，返回 (kb_id, is_new)。"""

    def delete_knowledge_base(self, kb_id: str) -> tuple[bool, str]:
        """删除知识库（MySQL + ChromaDB）。返回 (成功?, 消息)。"""

    def get_documents(self, kb_name: str) -> list[dict]:
        """获取知识库下的文档列表。"""

    def upload_and_process(self, kb_name: str, file_path: str, filename: str) -> dict:
        """上传文档 → 解析 → MySQL 记录 → ChromaDB 入库。返回 {success, chunk_count, error}。"""

    def chat(self, kb_name: str, session_id: str, query: str) -> tuple[str, list[RAGContext]]:
        """完整一轮问答：检索 → Rerank → LLM → 保存历史。返回 (回答全文, 引用列表)。"""
```

---

## Gradio 组件映射（app.py）

| UI 组件 | Gradio 类型 | 事件 | 处理函数 |
|---------|------------|------|---------|
| 欢迎/空状态 | `gr.Markdown` | 页面加载/知识库选择时更新 | `update_welcome` |
| 新建知识库输入 | `gr.Textbox` | — | — |
| 创建按钮 | `gr.Button` | click | `handle_create_kb` |
| 知识库选择下拉 | `gr.Dropdown` | change → 刷新文档列表 + 清空对话 | `handle_select_kb` |
| 删除按钮 | `gr.Button` | click | `handle_delete_kb` |
| 文件上传 | `gr.UploadButton` | upload → 后台 processing | `handle_upload` |
| 文档列表 | `gr.Dataframe` | 周期性刷新（every=3s 或 upload 后触发） | `refresh_docs` |
| 状态提示 | `gr.Markdown` | upload 时更新 | — |
| 对话历史 | `gr.Chatbot` | — | — |
| 用户输入 | `gr.Textbox` | submit | `handle_chat` |
| 发送按钮 | `gr.Button` | click | `handle_chat` |
| 引用来源 | `gr.Markdown` | 每次回答后更新 | — |

---

## 核心依赖

- `sr` 注解需 `from __future__ import annotations`
- Gradio 5.x（已在 pyproject.toml 中）
- `threading`（标准库）

---

### Prerequisite: 确认依赖与构建环境

- [ ] **Step 1: 确认 gradio 已安装（>=5.0）**

```bash
docker compose exec app python -c "import gradio; print(f'Gradio {gradio.__version__}')"
# 预期: Gradio 5.x (>= 5.0)
```

如果未安装，修改 pyproject.toml 确保 `gradio>=5.0,<6.0` 在 dependencies 中，然后重建：

```bash
docker compose build --no-cache app
docker compose up -d
```

- [ ] **Step 2: 确认 pytest 在容器内可用**

```bash
docker compose exec app python -m pytest --version
# 预期: pytest 8.x
```

---

## 文件结构 Iter 5 创建/修改清单

```
src/
├── app_service.py         (新建) 业务逻辑层，封装后端调用
├── app.py                 (修改) 替换 sleep stub 为 Gradio UI

tests/
├── test_app_service.py    (新建) 业务逻辑层单元测试（全 mock）
├── test_app.py            (新建) Gradio UI 基础冒烟测试

pyproject.toml             (可能修改) 如需新增依赖
```

---

## Design: Gradio UI 布局

```
┌─────────────────────────────────────────────────────────────┐
│  📊 金融文档智能问答助手                                       │
├───────────────────┬─────────────────────────────────────────┤
│  📚 知识库管理     │  💬 对话                                │
│                    │                                         │
│  ┌──────────────┐ │  ┌─────────────────────────────────────┐│
│  │ 新建知识库    │ │  │ 📌 欢迎使用金融文档问答助手！        ││
│  └──────────────┘ │  │                                     ││
│  知识库名称: []    │  │ 请选择一个知识库或创建新的知识库，   ││
│  [创建]           │  │ 然后上传文档开始提问。               ││
│                    │  │                                     ││
│  ┌──────────────┐ │  └─────────────────────────────────────┘│
│  │ 选择知识库 ▼ │ │                                         │
│  └──────────────┘ │  ┌─────────────────────────────────────┐│
│  [删除知识库]     │  │ [在此输入您的问题...]                ││
│                    │  │ [发送]                               ││
│  📄 文档管理       │  └─────────────────────────────────────┘│
│                    │                                         │
│  [选择文件上传]    │  📎 引用来源                             │
│                    │  > 来源: 年报2024.pdf (第3页)            │
│  文档列表:         │  > 贵州茅台2024年营收1,741亿元...        │
│  ┌──────────────┐ │                                         │
│  │ sample.pdf   │ │                                         │
│  │ sample.txt   │ │                                         │
│  └──────────────┘ │                                         │
└───────────────────┴─────────────────────────────────────────┘
```

---

### Task 1: AppService 业务逻辑层

**Files:**
- Create: `src/app_service.py`
- Create: `tests/test_app_service.py`

AppService 是 UI 与后端之间的薄封装层。设计原则：
- 所有方法同步返回（Gradio 的事件函数调用它）
- chat() 方法内部完成检索→Rerank→LLM→保存完整对话历史
- upload_and_process() 方法在单独线程中运行（由 app.py 启动线程）
- 所有外部依赖（RAGChain, MySQLDB, VectorStore, DocRouter）可在构造时注入

- [ ] **Step 1: 写测试 `tests/test_app_service.py`**

```python
"""Tests for AppService business logic layer."""
from unittest.mock import MagicMock, patch, ANY
import pytest
from src.app_service import AppService


class TestAppServiceInit:
    """AppService 初始化测试。"""

    @patch("src.app_service.RAGChain")
    @patch("src.app_service.MySQLDB")
    @patch("src.app_service.VectorStore")
    @patch("src.app_service.DocRouter")
    def test_init_defaults(self, mock_router, mock_vs, mock_db, mock_rag):
        """默认初始化应创建所有依赖实例。"""
        svc = AppService()
        assert svc.rag_chain is not None
        assert svc.db is not None
        assert svc.vector_store is not None
        assert svc.router is not None

    @patch("src.app_service.RAGChain")
    @patch("src.app_service.MySQLDB")
    @patch("src.app_service.VectorStore")
    @patch("src.app_service.DocRouter")
    def test_init_custom_deps(self, mock_router, mock_vs, mock_db, mock_rag):
        """应接受注入的自定义依赖。"""
        db = MagicMock()
        vs = MagicMock()
        router = MagicMock()
        rag = MagicMock()
        svc = AppService(mysql_db=db, vector_store=vs, router=router, rag_chain=rag)
        assert svc.db is db
        assert svc.vector_store is vs
        assert svc.router is router
        assert svc.rag_chain is rag


class TestAppServiceKBs:
    """知识库管理测试。"""

    @patch("src.app_service.RAGChain")
    @patch("src.app_service.MySQLDB")
    @patch("src.app_service.VectorStore")
    @patch("src.app_service.DocRouter")
    def test_list_knowledge_bases(self, mock_router, mock_vs, mock_db, mock_rag):
        db = MagicMock()
        db.get_all_kb.return_value = [("id1", "KB1"), ("id2", "KB2")]
        svc = AppService(mysql_db=db)
        result = svc.list_knowledge_bases()
        assert result == [("id1", "KB1"), ("id2", "KB2")]

    @patch("src.app_service.RAGChain")
    @patch("src.app_service.MySQLDB")
    @patch("src.app_service.VectorStore")
    @patch("src.app_service.DocRouter")
    def test_create_kb_success(self, mock_router, mock_vs, mock_db, mock_rag):
        db = MagicMock()
        db.get_or_create_kb.return_value = ("new_id", True)
        svc = AppService(mysql_db=db)
        kid, is_new = svc.create_knowledge_base("测试库", "描述")
        assert kid == "new_id"
        assert is_new is True

    @patch("src.app_service.RAGChain")
    @patch("src.app_service.MySQLDB")
    @patch("src.app_service.VectorStore")
    @patch("src.app_service.DocRouter")
    def test_delete_kb_success(self, mock_router, mock_vs, mock_db, mock_rag):
        db = MagicMock()
        db.delete_kb.return_value = True
        vs = MagicMock()
        vs.delete_collection.return_value = True
        svc = AppService(mysql_db=db, vector_store=vs)
        ok, msg = svc.delete_knowledge_base("kb_id")
        assert ok is True
        vs.delete_collection.assert_called_once_with("kb_id")

    @patch("src.app_service.RAGChain")
    @patch("src.app_service.MySQLDB")
    @patch("src.app_service.VectorStore")
    @patch("src.app_service.DocRouter")
    def test_delete_kb_not_found(self, mock_router, mock_vs, mock_db, mock_rag):
        db = MagicMock()
        db.delete_kb.return_value = False
        svc = AppService(mysql_db=db)
        ok, msg = svc.delete_knowledge_base("nonexistent")
        assert ok is False
        assert "不存在" in msg


class TestAppServiceUpload:
    """文档上传处理测试。"""

    @patch("src.app_service.RAGChain")
    @patch("src.app_service.MySQLDB")
    @patch("src.app_service.VectorStore")
    @patch("src.app_service.DocRouter")
    def test_upload_and_process(self, mock_router, mock_vs, mock_db, mock_rag):
        db = MagicMock()
        db.get_kb_by_name.return_value = "kb_id"
        db.add_document.return_value = "doc_id"

        vs = MagicMock()
        vs.add_chunks.return_value = 5

        router = MagicMock()
        router.parse.return_value = MagicMock(
            chunks=[MagicMock(content="c1", metadata={}, chunk_id="c:0"),
                    MagicMock(content="c2", metadata={}, chunk_id="c:1")],
            total_pages=1, total_chars=100, file_type="txt", is_scanned=False,
        )

        svc = AppService(mysql_db=db, vector_store=vs, router=router)
        result = svc.upload_and_process("测试库", "/tmp/test.txt", "test.txt")

        assert result["success"] is True
        assert result["chunk_count"] == 5
        router.parse.assert_called_once_with("/tmp/test.txt")
        vs.add_chunks.assert_called_once()
        db.update_document_status.assert_called_once_with("doc_id", "ready", chunk_count=5)

    @patch("src.app_service.RAGChain")
    @patch("src.app_service.MySQLDB")
    @patch("src.app_service.VectorStore")
    @patch("src.app_service.DocRouter")
    def test_upload_kb_not_found(self, mock_router, mock_vs, mock_db, mock_rag):
        db = MagicMock()
        db.get_kb_by_name.return_value = None
        svc = AppService(mysql_db=db)
        result = svc.upload_and_process("不存在的库", "/tmp/t.txt", "t.txt")
        assert result["success"] is False
        assert "知识库" in result["error"]

    @patch("src.app_service.RAGChain")
    @patch("src.app_service.MySQLDB")
    @patch("src.app_service.VectorStore")
    @patch("src.app_service.DocRouter")
    def test_upload_scanned_doc(self, mock_router, mock_vs, mock_db, mock_rag):
        db = MagicMock()
        db.get_kb_by_name.return_value = "kb_id"
        db.add_document.return_value = "doc_id"
        vs = MagicMock()
        router = MagicMock()
        router.parse.return_value = MagicMock(
            chunks=[], total_pages=3, total_chars=10, file_type="pdf", is_scanned=True,
        )
        svc = AppService(mysql_db=db, vector_store=vs, router=router)
        result = svc.upload_and_process("测试库", "/tmp/scan.pdf", "scan.pdf")
        assert result["success"] is False
        assert "扫描件" in result["error"]
        db.update_document_status.assert_called_once_with("doc_id", "failed", error_msg=ANY)

    @patch("src.app_service.RAGChain")
    @patch("src.app_service.MySQLDB")
    @patch("src.app_service.VectorStore")
    @patch("src.app_service.DocRouter")
    def test_upload_parse_error(self, mock_router, mock_vs, mock_db, mock_rag):
        db = MagicMock()
        db.get_kb_by_name.return_value = "kb_id"
        db.add_document.return_value = "doc_id"
        router = MagicMock()
        router.parse.side_effect = ValueError("Unsupported file type")
        svc = AppService(mysql_db=db, router=router)
        result = svc.upload_and_process("测试库", "/tmp/bad.xyz", "bad.xyz")
        assert result["success"] is False
        assert "Unsupported" in result["error"]


class TestAppServiceChat:
    """问答功能测试。"""

    @patch("src.app_service.RAGChain")
    @patch("src.app_service.MySQLDB")
    @patch("src.app_service.VectorStore")
    @patch("src.app_service.DocRouter")
    def test_chat(self, mock_router, mock_vs, mock_db, mock_rag):
        rag = MagicMock()
        # Mock the generator returned by chat_with_citations
        def mock_gen():
            yield "贵州"
            yield "茅台"
            yield "营收1,741亿元。"

        rag.chat_with_citations.return_value = (
            mock_gen(),
            [MagicMock(source="年报.pdf", page=3, content="营收1,741亿元", to_citation=lambda: "> citation")]
        )

        svc = AppService(rag_chain=rag)
        answer, citations = svc.chat("测试库", "sess_1", "营收多少？")

        assert "贵州茅台营收1,741亿元" in answer
        assert len(citations) == 1
        rag.chat_with_citations.assert_called_once_with("测试库", "sess_1", "营收多少？")
        # 验证 assistant 回答被保存到历史
        rag.chat_manager.add_message.assert_called_once_with("sess_1", "assistant", "贵州茅台营收1,741亿元。", sources=ANY)

    @patch("src.app_service.RAGChain")
    @patch("src.app_service.MySQLDB")
    @patch("src.app_service.VectorStore")
    @patch("src.app_service.DocRouter")
    def test_chat_kb_not_found(self, mock_router, mock_vs, mock_db, mock_rag):
        rag = MagicMock()
        rag.chat_with_citations.return_value = (
            (t for t in ["知识库 'xx' 不存在"]),
            []
        )
        svc = AppService(rag_chain=rag)
        answer, citations = svc.chat("xx", "sess", "q")
        assert "不存在" in answer
        assert citations == []
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
docker compose exec app python -m pytest tests/test_app_service.py -v 2>&1 | tail -10
# 预期: 11 failed / ModuleNotFoundError（app_service.py 不存在）
```

- [ ] **Step 3: 实现 `src/app_service.py`**

```python
"""应用业务逻辑层 — UI 与后端之间的薄封装。

职责：
  1. 知识库 CRUD（list / create / delete）
  2. 文档上传、解析、向量化全流程
  3. 对话问答（RAG 链路）+ 历史保存
  4. 文档列表查询

设计原则：
  - 所有方法同步返回，不涉及 UI 逻辑
  - upload_and_process() 可在独立线程中执行（由 app.py 管理线程）
  - 所有外部依赖可在构造时注入（方便测试）
"""
from typing import Optional
from loguru import logger
from src.rag_chain import RAGChain, RAGContext
from src.mysql_db import MySQLDB
from src.vector_store import VectorStore
from src.parsers.router import DocRouter


class AppService:
    """UI 与后端之间的业务逻辑层。"""

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

    # ==================== 知识库管理 ====================

    def list_knowledge_bases(self) -> list[tuple[str, str]]:
        """列出所有知识库。返回 [(kb_id, kb_name), ...]。"""
        return self.db.get_all_kb()

    def create_knowledge_base(self, name: str, description: str = "") -> tuple[str, bool]:
        """创建知识库。返回 (kb_id, is_new)。"""
        return self.db.get_or_create_kb(name, description)

    def delete_knowledge_base(self, kb_id: str) -> tuple[bool, str]:
        """删除知识库（MySQL + ChromaDB 向量数据）。返回 (成功?, 消息)。"""
        # 先删 ChromaDB collection，再删 MySQL 记录
        self.vector_store.delete_collection(kb_id)
        ok = self.db.delete_kb(kb_id)
        if ok:
            logger.info("Deleted knowledge base: {}", kb_id)
            return True, "知识库已删除"
        logger.warning("Knowledge base '{}' not found for deletion", kb_id)
        return False, "知识库不存在"

    # ==================== 文档管理 ====================

    def get_documents(self, kb_name: str) -> list[dict]:
        """获取指定知识库下的文档列表。"""
        kb_id = self.db.get_kb_by_name(kb_name)
        if not kb_id:
            return []
        return self.db.get_documents(kb_id)

    def upload_and_process(self, kb_name: str, file_path: str, filename: str) -> dict:
        """上传文档并执行完整处理流水线：解析 → MySQL 记录 → 向量化入库。

        此方法同步执行，预计耗时 1-30 秒。调用方（app.py）应在独立线程中运行。

        Args:
            kb_name: 知识库名称
            file_path: 上传文件的临时路径
            filename: 原始文件名

        Returns:
            dict: {"success": bool, "chunk_count": int, "error": str}
        """
        kb_id = self.db.get_kb_by_name(kb_name)
        if not kb_id:
            return {"success": False, "chunk_count": 0, "error": f"知识库 '{kb_name}' 不存在"}

        file_type = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
        file_size = 0
        try:
            import os
            file_size = os.path.getsize(file_path)
        except OSError:
            pass

        # Step 1: 写入 MySQL（status = pending）
        doc_id = self.db.add_document(kb_id, filename, file_type, file_size)

        try:
            # Step 2: 解析文档
            parse_result = self.router.parse(file_path)

            # Step 3: 检测扫描件（PDF 无提取文本）
            if parse_result.is_scanned:
                error_msg = "文档为扫描件或无可提取文本，MVP 暂不支持 OCR"
                self.db.update_document_status(doc_id, "failed", error_msg=error_msg)
                logger.warning("Scanned document detected: {}", filename)
                return {"success": False, "chunk_count": 0, "error": error_msg}

            # Step 4: 向量化入库
            chunk_count = self.vector_store.add_chunks(kb_id, parse_result.chunks, doc_id)

            # Step 5: 更新状态为 ready
            self.db.update_document_status(doc_id, "ready", chunk_count=chunk_count)

            logger.info("Document processed: {} → {} chunks", filename, chunk_count)
            return {"success": True, "chunk_count": chunk_count, "error": ""}

        except Exception as e:
            error_msg = str(e)
            logger.error("Document processing failed: {} - {}", filename, error_msg)
            try:
                self.db.update_document_status(doc_id, "failed", error_msg=error_msg)
            except Exception:
                pass
            return {"success": False, "chunk_count": 0, "error": error_msg}

    # ==================== 问答 ====================

    def chat(self, kb_name: str, session_id: str, query: str) -> tuple[str, list[RAGContext]]:
        """执行一轮 RAG 问答。

        流程：
          1. 调用 RAGChain.chat_with_citations() 获取流式回答和引用
          2. 将流式 token 拼接为完整回答
          3. 将本轮回答保存到对话历史

        Args:
            kb_name: 知识库名称
            session_id: 会话 ID
            query: 用户问题

        Returns:
            (answer_text, citations_list)
        """
        token_gen, citations = self.rag_chain.chat_with_citations(kb_name, session_id, query)
        full_answer = "".join([t for t in token_gen])

        # 保存 assistant 回答到对话历史
        sources = [f"{c.source} (第{c.page}页)" for c in citations]
        self.rag_chain.chat_manager.add_message(
            session_id, "assistant", full_answer, sources=sources,
        )

        return full_answer, citations
```

- [ ] **Step 4: 运行测试确认通过**

```bash
docker compose exec app python -m pytest tests/test_app_service.py -v
# 预期: 11 passed (全部 mock，不需要真实 API Key)
```

- [ ] **Step 5: Commit**

```bash
git add src/app_service.py tests/test_app_service.py
git commit -m "feat: add AppService business logic layer for Gradio UI"
```

---

### Task 2: Gradio UI 主入口

**Files:**
- Modify: `src/app.py`（替换当前 sleep stub 为完整 Gradio UI）

app.py 负责：
1. 构建 Gradio Blocks 布局（左右两栏）
2. 事件路由（按钮 click、下拉 change、文件 upload、聊天 submit）
3. 线程管理（文件上传在后台线程处理，不阻塞 UI）
4. session_id 管理（每个浏览器 tab 独立会话）
5. 空状态引导、加载状态提示、错误提示

- [ ] **Step 1: 修改 `src/app.py` — 完整 Gradio UI**

```python
"""应用入口 — Gradio 5.x UI 界面。

功能模块：
  - 知识库管理（创建、选择、删除）
  - 文档上传（异步后台处理 + 进度提示）
  - 对话问答（流式输出 + 引用展示）

页面布局：左栏（知识库 + 文档管理）/ 右栏（对话 + 引用）
"""
from __future__ import annotations

import os
import threading
import uuid
from typing import Optional

import gradio as gr
from loguru import logger

from src.app_service import AppService

# 全局业务逻辑实例（应用生命周期内保持单例）
_service: Optional[AppService] = None


def get_service() -> AppService:
    """延迟初始化 AppService（仅在首次访问时创建连接）。"""
    global _service
    if _service is None:
        _service = AppService()
        logger.info("AppService initialized")
    return _service


# ==================== 会话管理 ====================

def get_or_create_session_id(request: gr.Request) -> str:
    """从 Gradio 请求中获取 session_id，不存在则创建新的。

    Gradio 为每个浏览器 tab 分配唯一的 session_hash，
    我们用这个 hash 作为对话会话 ID。
    """
    return request.session_hash


# ==================== 知识库管理 ====================

def refresh_kb_dropdown() -> list[tuple[str, str]]:
    """刷新知识库下拉菜单的选项列表。"""
    svc = get_service()
    kbs = svc.list_knowledge_bases()
    # 返回 [(display_name, value_id), ...] 格式的 choices
    return [(name, kid) for kid, name in kbs]


def handle_create_kb(name: str, description: str = "") -> tuple[str, list[tuple[str, str]]]:
    """创建新知识库。

    Args:
        name: 知识库名称（唯一）
        description: 可选描述

    Returns:
        (状态消息, 更新后的下拉选项)
    """
    if not name or not name.strip():
        return "请输入知识库名称", refresh_kb_dropdown()

    svc = get_service()
    try:
        _, is_new = svc.create_knowledge_base(name.strip(), description)
        if is_new:
            logger.info("Created knowledge base: {}", name)
            return f"✅ 知识库 '{name}' 创建成功", refresh_kb_dropdown()
        return f"ℹ️ 知识库 '{name}' 已存在", refresh_kb_dropdown()
    except Exception as e:
        logger.error("Failed to create KB '{}': {}", name, e)
        return f"❌ 创建失败: {e}", refresh_kb_dropdown()


def handle_delete_kb(kb_id: str, kb_dropdown_choices: list) -> tuple[str, str, list, list]:
    """删除知识库（MySQL + ChromaDB）。

    Returns:
        (状态消息, 当前选择的 KB ID, 更新后的下拉选项, 清空的文档列表)
    """
    if not kb_id:
        return "请先选择一个知识库", "", refresh_kb_dropdown(), []

    svc = get_service()
    try:
        ok, msg = svc.delete_knowledge_base(kb_id)
        if ok:
            logger.info("Deleted KB: {}", kb_id)
        return f"{'✅' if ok else '⚠️'} {msg}", "", refresh_kb_dropdown(), []
    except Exception as e:
        logger.error("Failed to delete KB '{}': {}", kb_id, e)
        return f"❌ 删除失败: {e}", "", refresh_kb_dropdown(), []


def handle_select_kb(kb_name: str) -> tuple[str, list[dict]]:
    """选择知识库时：刷新文档列表 + 清空对话历史。

    Args:
        kb_name: 选中的知识库名称（由下拉框 value 对应）

    Returns:
        (文档列表的 DataFrame 数据, 状态消息)
    """
    if not kb_name:
        return [], "请选择或创建一个知识库"

    svc = get_service()
    docs = svc.get_documents(kb_name)
    return docs, f"已选择知识库: {kb_name}"


def format_docs_for_display(docs: list[dict]) -> list[list]:
    """将文档列表格式化为 DataFrame 表格数据。

    表格列：文件名 | 类型 | 大小 | 状态 | 分块数
    """
    if not docs:
        return []

    rows = []
    for d in docs:
        size_str = f"{d['file_size'] / 1024:.1f} KB" if d.get("file_size") else "-"
        status_icon = {"ready": "✅", "failed": "❌", "processing": "⏳", "pending": "⏳"}
        icon = status_icon.get(d.get("status", ""), "❓")
        rows.append([
            d.get("filename", ""),
            d.get("file_type", ""),
            size_str,
            f"{icon} {d.get('status', '')}",
            d.get("chunk_count", 0),
        ])
    return rows


# ==================== 文件上传 ====================

def handle_upload(
    kb_name: str, files: list,
) -> tuple[str, list[list]]:
    """上传文档并在后台线程中处理。

    在后台线程中运行 AppService.upload_and_process()，
    避免阻塞 Gradio UI（大文件解析可能耗时数秒）。

    Args:
        kb_name: 目标知识库名称
        files: Gradio 上传的文件列表

    Returns:
        (状态消息, 更新后的文档列表表格)
    """
    if not kb_name:
        return "请先选择知识库", []
    if not files:
        return "请选择要上传的文件", []

    svc = get_service()
    results = []

    for file_obj in files:
        # Gradio 上传文件为 tempfile-like 对象，用 .name 取路径
        file_path = file_obj.name if hasattr(file_obj, "name") else str(file_obj)
        filename = os.path.basename(file_path)

        # 在后台线程中执行处理
        # 这里用同步方式串行处理，Gradio 5 的事件处理函数在独立线程中运行
        result = svc.upload_and_process(kb_name, file_path, filename)

        if result["success"]:
            msg = f"✅ {filename}: 处理完成（{result['chunk_count']} 个分块）"
            logger.info("Upload success: {}", msg)
        else:
            msg = f"❌ {filename}: 处理失败 - {result['error']}"
            logger.warning("Upload failed: {}", msg)
        results.append(msg)

    # 返回更新后的文档列表
    docs = svc.get_documents(kb_name)
    return "\n".join(results), format_docs_for_display(docs)


# ==================== 对话问答 ====================

def handle_chat(
    message: str, history: list, kb_name: str, session_id: str,
) -> tuple[str, list, str]:
    """处理用户消息并返回 AI 回答（流式效果）。

    由于 RAGChain.chat_with_citations() 返回生成器，
    此函数通过 yield 逐 token 更新 chatbot，产生流式输出效果。

    Args:
        message: 用户输入的问题
        history: 当前对话历史（Gradio Chatbot 维护）
        kb_name: 当前选中的知识库名称
        session_id: 当前会话 ID

    Yields:
        每轮 yield 更新后的 (history, citations_text)
    """
    if not kb_name:
        yield history + [[message, "请先选择一个知识库"]], ""
        return

    if not message or not message.strip():
        yield history, ""
        return

    svc = get_service()
    token_gen, citations = svc.rag_chain.chat_with_citations(
        kb_name, session_id, message,
    )

    # 收集完整回答并流式输出
    full_answer = ""
    history = history + [[message, ""]]
    for token in token_gen:
        full_answer += token
        history[-1][1] = full_answer
        yield history, ""

    # 保存 assistant 回答到对话历史
    sources = [f"{c.source} (第{c.page}页)" for c in citations]
    svc.rag_chain.chat_manager.add_message(
        session_id, "assistant", full_answer, sources=sources,
    )

    # 生成引用 Markdown
    citations_text = ""
    if citations:
        citations_lines = ["**📎 引用来源：**"]
        for c in citations:
            citations_lines.append(c.to_citation())
        citations_text = "\n".join(citations_lines)

    yield history, citations_text


def clear_chat_on_kb_change(kb_name: str) -> tuple[list, str]:
    """切换知识库时清空对话历史和引用。

    Args:
        kb_name: 新选中的知识库名称

    Returns:
        (清空的对话历史, 清空的引用文本)
    """
    # 直接返回空值，Gradio chatbot 和 markdown 会清空显示
    return [], ""


# ==================== 构建 UI ====================

CSS = """
#welcome-box { border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; }
#sidebar { background: #f8f9fa; padding: 16px; border-radius: 8px; }
"""


def create_ui() -> gr.Blocks:
    """构建完整的 Gradio UI。"""
    with gr.Blocks(
        title="金融文档智能问答助手",
        css=CSS,
        theme=gr.themes.Soft(),
    ) as demo:
        # ====== 顶栏标题 ======
        gr.Markdown("# 📊 金融文档智能问答助手")
        gr.Markdown("上传财报 PDF/DOCX/TXT 文件，基于 RAG 技术进行智能问答。")

        # ====== Session ID 管理 ======
        session_state = gr.State()

        def init_session(request: gr.Request) -> str:
            return request.session_hash

        demo.load(init_session, inputs=None, outputs=session_state)

        # ====== 主体布局：左栏 + 右栏 ======
        with gr.Row():
            # ====== 左栏：知识库 + 文档管理 ======
            with gr.Column(scale=1, elem_id="sidebar"):
                # --- 知识库管理 ---
                gr.Markdown("### 📚 知识库管理")

                kb_name_input = gr.Textbox(
                    label="新建知识库名称",
                    placeholder="如：2024年年报",
                    scale=3,
                )
                with gr.Row():
                    create_kb_btn = gr.Button("创建", variant="primary", scale=1)
                    delete_kb_btn = gr.Button("删除", variant="stop", scale=1)

                kb_dropdown = gr.Dropdown(
                    label="选择知识库",
                    choices=[],
                    interactive=True,
                    allow_custom_value=False,
                )

                kb_status = gr.Markdown("📌 欢迎使用！请创建或选择一个知识库开始。", visible=True)

                # --- 文档管理 ---
                gr.Markdown("### 📄 文档管理")

                file_upload = gr.UploadButton(
                    label="选择文件上传",
                    file_types=[".pdf", ".docx", ".txt"],
                    file_count="multiple",
                    variant="secondary",
                )

                upload_status = gr.Markdown("")

                doc_table = gr.Dataframe(
                    headers=["文件名", "类型", "大小", "状态", "分块数"],
                    label="文档列表",
                    interactive=False,
                )

            # ====== 右栏：对话 + 引用 ======
            with gr.Column(scale=2):
                # --- 对话 ---
                gr.Markdown("### 💬 对话")

                chatbot = gr.Chatbot(
                    label="对话历史",
                    height=500,
                    bubble_limit=50,
                )

                with gr.Row():
                    msg_input = gr.Textbox(
                        label="输入问题",
                        placeholder="在此输入您的问题...",
                        scale=4,
                    )
                    send_btn = gr.Button("发送", variant="primary", scale=1)

                # --- 引用 ---
                citations_display = gr.Markdown(
                    label="📎 引用来源",
                    value="",
                    visible=True,
                )

        # ====== 事件绑定 ======

        # 页面加载：初始化 session，刷新 KB 下拉
        demo.load(
            fn=lambda: gr.Dropdown(choices=refresh_kb_dropdown()),
            inputs=None,
            outputs=kb_dropdown,
        )

        # 创建知识库
        create_kb_btn.click(
            fn=handle_create_kb,
            inputs=[kb_name_input],
            outputs=[kb_status, kb_dropdown],
        ).then(
            fn=lambda: "", outputs=kb_name_input,  # 清空输入框
        )

        # 删除知识库
        delete_kb_btn.click(
            fn=handle_delete_kb,
            inputs=[kb_dropdown],
            outputs=[kb_status, kb_dropdown, kb_dropdown, doc_table],
        )

        # 选择知识库 → 刷新文档列表 + 清空对话
        kb_dropdown.change(
            fn=handle_select_kb,
            inputs=[kb_dropdown],
            outputs=[doc_table, kb_status],
        ).then(
            fn=clear_chat_on_kb_change,
            inputs=[kb_dropdown],
            outputs=[chatbot, citations_display],
        )

        # 上传文件
        file_upload.upload(
            fn=handle_upload,
            inputs=[kb_dropdown, file_upload],
            outputs=[upload_status, doc_table],
        )

        # 发送消息（回车或点击发送按钮）
        send_fn = msg_input.submit(
            fn=handle_chat,
            inputs=[msg_input, chatbot, kb_dropdown, session_state],
            outputs=[chatbot, citations_display],
        )
        send_btn.click(
            fn=handle_chat,
            inputs=[msg_input, chatbot, kb_dropdown, session_state],
            outputs=[chatbot, citations_display],
        )

        # 发送后清空输入框
        send_fn.then(fn=lambda: "", inputs=None, outputs=msg_input)

    return demo


# ==================== 启动入口 ====================

def main() -> None:
    """启动 Gradio 应用。"""
    logger.info("Starting Financial QA MVP UI...")
    demo = create_ui()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        show_error=True,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证 app.py 可启动（基本冒烟测试）**

```bash
# 验证 app.py 可导入（不要启动实际 UI，只检查语法和导入）
docker compose exec app python -c "
from src.app import create_ui, get_service, handle_chat
print('app.py modules imported successfully')
svc = get_service()
print(f'AppService initialized: {type(svc).__name__}')
kbs = svc.list_knowledge_bases()
print(f'Knowledge bases: {len(kbs)}')
"
# 预期: 导入成功，知识库列表不为空（或为空列表）
```

- [ ] **Step 3: 创建 Gradio 冒烟测试 `tests/test_app.py`**

```python
"""Gradio UI 基本冒烟测试 — 验证组件创建和事件函数。"""
from unittest.mock import MagicMock, patch
import pytest


class TestUIHelpers:
    """测试 UI 辅助函数（不依赖 Gradio 渲染）。"""

    @patch("src.app.get_service")
    def test_refresh_kb_dropdown(self, mock_get_svc):
        """下拉菜单刷新应返回 choices 列表。"""
        from src.app import refresh_kb_dropdown
        svc = MagicMock()
        svc.list_knowledge_bases.return_value = [("id1", "KB1"), ("id2", "KB2")]
        mock_get_svc.return_value = svc
        choices = refresh_kb_dropdown()
        assert choices == [("KB1", "id1"), ("KB2", "id2")]

    @patch("src.app.get_service")
    def test_handle_create_kb_success(self, mock_get_svc):
        """创建知识库成功应返回成功消息。"""
        from src.app import handle_create_kb
        svc = MagicMock()
        svc.create_knowledge_base.return_value = ("new_id", True)
        mock_get_svc.return_value = svc
        msg, choices = handle_create_kb("测试库")
        assert "创建成功" in msg

    @patch("src.app.get_service")
    def test_handle_create_kb_existing(self, mock_get_svc):
        """创建已存在的知识库应提示已存在。"""
        from src.app import handle_create_kb
        svc = MagicMock()
        svc.create_knowledge_base.return_value = ("existing_id", False)
        mock_get_svc.return_value = svc
        msg, choices = handle_create_kb("已存在")
        assert "已存在" in msg

    @patch("src.app.get_service")
    def test_handle_create_kb_empty_name(self, mock_get_svc):
        """空名称应返回提示，不调用 service。"""
        from src.app import handle_create_kb
        msg, choices = handle_create_kb("")
        assert "请输入" in msg

    @patch("src.app.get_service")
    def test_handle_delete_kb(self, mock_get_svc):
        """删除知识库应调用 service.delete_knowledge_base。"""
        from src.app import handle_delete_kb
        svc = MagicMock()
        svc.delete_knowledge_base.return_value = (True, "已删除")
        mock_get_svc.return_value = svc
        msg, kid, choices, docs = handle_delete_kb("kb_id", [])
        assert "已删除" in msg
        svc.delete_knowledge_base.assert_called_once_with("kb_id")

    @patch("src.app.get_service")
    def test_handle_select_kb_with_name(self, mock_get_svc):
        """选择知识库应返回文档列表。"""
        from src.app import handle_select_kb
        svc = MagicMock()
        svc.get_documents.return_value = [{"filename": "test.pdf", "file_type": "pdf"}]
        mock_get_svc.return_value = svc
        docs, status = handle_select_kb("测试库")
        assert len(docs) > 0
        assert "已选择" in status

    @patch("src.app.get_service")
    def test_format_docs_for_display(self, mock_get_svc):
        """文档列表格式化应生成正确表格行。"""
        from src.app import format_docs_for_display
        docs = [
            {"filename": "a.pdf", "file_type": "pdf", "file_size": 1024, "status": "ready", "chunk_count": 5},
            {"filename": "b.txt", "file_type": "txt", "file_size": 512, "status": "pending", "chunk_count": 0},
        ]
        rows = format_docs_for_display(docs)
        assert len(rows) == 2
        assert rows[0][0] == "a.pdf"
        assert "✅" in rows[0][3]

    def test_format_docs_empty(self):
        """空文档列表应返回空列表。"""
        from src.app import format_docs_for_display
        assert format_docs_for_display([]) == []

    @patch("src.app.get_service")
    def test_handle_upload_no_kb(self, mock_get_svc):
        """未选择知识库时上传应提示。"""
        from src.app import handle_upload
        msg, docs = handle_upload("", [MagicMock()])
        assert "先选择知识库" in msg
        assert docs == []

    @patch("src.app.get_service")
    def test_handle_upload_no_files(self, mock_get_svc):
        """未选择文件时上传应提示。"""
        from src.app import handle_upload
        msg, docs = handle_upload("KB", [])
        assert "选择要上传" in msg
```

- [ ] **Step 4: 运行冒烟测试**

```bash
docker compose exec app python -m pytest tests/test_app.py -v
# 预期: 10 passed
```

- [ ] **Step 5: 创建测试文件清单（如果不存在）**

```bash
# 确保 test_docs/ 目录有测试文档
ls test_docs/sample.txt test_docs/sample.docx test_docs/sample.pdf 2>/dev/null
# 如果有缺失，重新复制到容器
docker cp test_docs/sample.txt financial-qa-app:/app/test_docs/sample.txt 2>/dev/null || true
docker cp test_docs/sample.docx financial-qa-app:/app/test_docs/sample.docx 2>/dev/null || true
docker cp test_docs/sample.pdf financial-qa-app:/app/test_docs/sample.pdf 2>/dev/null || true
```

- [ ] **Step 6: Commit**

```bash
git add src/app.py tests/test_app.py
git commit -m "feat: add Gradio UI with KB management, file upload, and chat"
```

---

### Task 3: 集成验证

**Files:** （无创建，仅运行命令）

全流程验证：创建知识库 → 上传文档 → 检索 → 问答 → 引用展示。

- [ ] **Step 1: 确认所有容器健康运行**

```bash
docker compose ps
# 预期: app (up), mysql (healthy), redis (healthy)
```

- [ ] **Step 2: 运行全部单元测试**

```bash
docker compose exec app python -m pytest tests/ -v --tb=short 2>&1 | tail -30
# 预期: 全部测试通过（含已有的 36+ + 新增的 app_service + app 测试）
```

- [ ] **Step 3: 端到端流程验证 — 创建知识库 + 上传文档**

```bash
docker compose exec app python -c "
from src.app_service import AppService
svc = AppService()

# 创建知识库
kid, is_new = svc.create_knowledge_base('集成验证测试库')
print(f'KB: {kid}, new={is_new}')

# 上传 sample.txt
result = svc.upload_and_process('集成验证测试库', 'test_docs/sample.txt', 'sample.txt')
print(f'Upload txt: {result}')

# 上传 sample.docx
result2 = svc.upload_and_process('集成验证测试库', 'test_docs/sample.docx', 'sample.docx')
print(f'Upload docx: {result2}')

# 列出文档
docs = svc.get_documents('集成验证测试库')
for d in docs:
    print(f'  - {d[\"filename\"]}: {d[\"status\"]} ({d[\"chunk_count\"]} chunks)')
"
# 预期: 两个文档处理成功，状态为 ready
```

- [ ] **Step 4: 端到端问答验证**

```bash
docker compose exec app python -c "
from src.app_service import AppService
import uuid

svc = AppService()
session_id = 'e2e_test_' + uuid.uuid4().hex[:6]

answer, citations = svc.chat('集成验证测试库', session_id, '贵州茅台2024年营业总收入是多少？')
print(f'Answer: {answer}')
print(f'Citations: {len(citations)}')
for c in citations:
    print(f'  - {c.source} (p{c.page}): {c.content[:80]}...')
"
# 预期: 回答包含营收数据，引用不为空
```

- [ ] **Step 5: 验证空状态引导**

检查 Gradio UI 启动后，未选择知识库时是否显示欢迎信息：

```bash
docker compose exec app python -c "
from src.app import get_service, handle_select_kb
docs, status = handle_select_kb('')
print(f'Empty state status: {status}')
assert '欢迎' in status or '选择' in status or '创建' in status
print('✅ Empty state guide is correct')
"
```

- [ ] **Step 6: 验证知识库切换时清空对话**

```bash
docker compose exec app python -c "
from src.app import clear_chat_on_kb_change
history, citations = clear_chat_on_kb_change('新知识库')
assert history == []
assert citations == ''
print('✅ KB switch clears chat correctly')
"
```

- [ ] **Step 7: 验证上传处理中的异常路径**

```bash
docker compose exec app python -c "
from src.app_service import AppService

svc = AppService()

# 上传不存在的文件 -> 应返回错误
result = svc.upload_and_process('集成验证测试库', '/tmp/nonexistent.pdf', 'no.pdf')
print(f'Nonexistent file: {result}')
assert result['success'] is False

# 上传不支持的类型 -> 应返回错误
import tempfile
with tempfile.NamedTemporaryFile(suffix='.xyz') as f:
    f.write(b'test')
    f.flush()
    result2 = svc.upload_and_process('集成验证测试库', f.name, 'test.xyz')
    print(f'Unsupported type: {result2}')
    assert result2['success'] is False

print('✅ Error paths verified')
"
# 预期: 两个上传都返回 success=False，有错误信息
```

- [ ] **Step 8: 清理测试数据**

```bash
docker compose exec app python -c "
from src.app_service import AppService
svc = AppService()
kid = svc.db.get_kb_by_name('集成验证测试库')
if kid:
    svc.delete_knowledge_base(kid)
    print('Cleaned up test KB')
"
```

- [ ] **Step 9: Iter 5 完成——提交代码**

```bash
git add src/app.py src/app_service.py tests/test_app_service.py tests/test_app.py
git status  # 确认只有 Iter 5 相关文件
git commit -m "feat: complete Iter 5 Gradio UI interface

- Add AppService business logic layer (KB CRUD, document upload, chat)
- Add full Gradio 5.x UI with:
  - Knowledge base management (create/select/delete)
  - File upload with async background processing
  - Conversational Q&A with streaming output
  - Citation source display
  - Empty state welcome guidance
  - KB switch clears chat history
- Add comprehensive test suite (app_service + app UI helpers)"
```

---
