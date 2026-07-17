# API 单元测试补齐实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复所有 API 测试的 patch 路径错误，补齐缺失端点的单元测试

**Architecture:** 每个测试文件对应一组 API 端点，使用 FastAPI TestClient + `@patch` 做依赖隔离。mock 数据统一存放在 `tests/api/mock_data.py`，通过工厂函数按需生成。`conftest.py` 提供 `client` / `auth_client` fixture。

**Tech Stack:** pytest 8.4 / FastAPI TestClient / unittest.mock

## Global Constraints

- 所有 mock 的 patch 路径必须映射到 `src.api.*`（不是 `src.api.routes.*`）
- 每个端点至少包含 Happy path + 一个错误场景两个测试
- 测试只测 API 响应层（HTTP 状态码 + JSON body 结构），不测 services/infra 逻辑
- 所有 mock 数据通过 `tests/api/mock_data.py` 工厂函数生成
- 新增测试文件统一使用 `conftest.py` fixture
- 运行 `python -m pytest tests/api/ -v` 所有测试通过

---

### Task 1: conftest.py — 创建公共测试基础设施

**Files:**
- Create: `tests/api/conftest.py`

```python
"""API 测试公共基础 — TestClient + auth 辅助函数。"""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from src.main import app


@pytest.fixture
def client() -> TestClient:
    """返回裸 TestClient（无认证）。"""
    return TestClient(app)


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    """返回带认证 Cookie 的 TestClient。

    自动 patch 中间件的 token 校验，模拟 'test-user-id' 用户已登录的状态。
    适用于 kb / documents / sessions 等需要登录态的端点。
    """
    client.cookies.set("token", "test-token")
    patcher = patch(
        "src.middleware.auth.UserAuth.get_user_id_from_token_async",
        new_callable=AsyncMock,
        return_value="test-user-id",
    )
    patcher.start()
    yield client
    patcher.stop()
    client.cookies.clear()
```

- [ ] **Step 1: Write `tests/api/conftest.py`**
- [ ] **Step 2: Verify fixtures load**

Run: `python -m pytest tests/api/ --collect-only -q 2>&1 | head -10`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add tests/api/conftest.py
git commit -m "test(api): add conftest with client/auth_client fixtures"
```

---

### Task 2: mock_data.py — 统一 mock 数据工厂

**Files:**
- Create: `tests/api/mock_data.py`

```python
"""API 测试统一 mock 数据工厂。

所有 mock 数据集中管理，各测试文件通过 import 引用。
工厂函数支持 **kw 参数，按需覆盖默认字段。
"""

from datetime import datetime
from decimal import Decimal


def make_kb(id="kb-1", name="年报知识库", doc_count=0):
    """创建模拟知识库数据。"""
    return {"id": id, "name": name, "doc_count": doc_count}


def make_doc(id="doc-1", filename="test.pdf", status="ready", **kw):
    """创建模拟文档数据。"""
    base = {
        "id": id, "filename": filename, "file_type": "pdf",
        "file_size": 1024, "status": status, "chunk_count": 10,
        "created_at": datetime(2026, 7, 1),
    }
    base.update(kw)
    return base


def make_chunk(id="c1", content="test", page=1, parent_content=None):
    """创建模拟分块数据。"""
    chunk = {
        "id": id, "content": content,
        "metadata": {"page": page, "tokens": len(content), "block_type": "text"},
    }
    if parent_content:
        chunk["metadata"]["parent_content"] = parent_content
    return chunk


def make_session(id="s1", title="财报问答", **kw):
    """创建模拟会话数据。"""
    base = {
        "id": id, "title": title, "kb_id": "kb-1", "kb_name": "年报",
        "message_count": 3,
        "created_at": datetime(2026, 1, 1),
        "updated_at": datetime(2026, 1, 2),
    }
    base.update(kw)
    return base


def make_message(role="user", content="hello", **kw):
    """创建模拟消息数据。"""
    base = {
        "role": role, "content": content, "sources": None,
        "created_at": datetime(2026, 1, 1),
    }
    base.update(kw)
    return base


def make_eval_report(overall_score=0.84, passed=True, **kw):
    """创建模拟评估报告数据。"""
    base = {
        "eval_date": datetime(2026, 6, 15),
        "faithfulness": Decimal("0.85"),
        "answer_relevancy": Decimal("0.90"),
        "context_precision": Decimal("0.78"),
        "context_recall": Decimal("0.82"),
        "overall_score": Decimal(str(overall_score)),
        "passed": passed,
        "qa_count": 20,
        "run_type": "full",
    }
    base.update(kw)
    return base


def make_user(id="u1", account="test", password="hashed_pwd"):
    """创建模拟用户数据。"""
    return {"id": id, "account": account, "password": password}
```

- [ ] **Step 1: Write `tests/api/mock_data.py`**
- [ ] **Step 2: Verify import**

Run: `python -c "from tests.api.mock_data import make_kb, make_doc, make_chunk, make_session, make_message, make_eval_report, make_user; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add tests/api/mock_data.py
git commit -m "test(api): add unified mock data factory"
```

---

### Task 3: 修复 test_knowledge_base.py

**Files:**
- Modify: `tests/api/test_knowledge_base.py`

完整替换文件内容：
```python
"""Tests for KB CRUD endpoints."""

from unittest.mock import AsyncMock, patch

from tests.api.mock_data import make_kb


@patch("src.api.knowledge_base._get_service")
def test_list_kbs(mock_get_service, auth_client):
    """POST /api/kbs/list 返回知识库列表。"""
    mock_svc = mock_get_service.return_value
    mock_svc.list_knowledge_bases = AsyncMock(return_value=[
        make_kb("kb-1", "年报知识库", doc_count=5),
        make_kb("kb-2", "财报知识库", doc_count=3),
    ])

    response = auth_client.post("/api/kbs/list", json={})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["name"] == "年报知识库"


@patch("src.api.knowledge_base._get_service")
def test_create_kb(mock_get_service, auth_client):
    """POST /api/kbs 创建新知识库。"""
    mock_svc = mock_get_service.return_value
    mock_svc.create_knowledge_base = AsyncMock(return_value=("new-kb-uuid", True))

    response = auth_client.post(
        "/api/kbs", json={"name": "测试库", "description": "测试"}
    )

    assert response.status_code == 201
    assert response.json()["data"] == {"id": "new-kb-uuid", "created": True}


@patch("src.api.knowledge_base._get_service")
def test_create_kb_missing_name(mock_get_service, auth_client):
    """POST /api/kbs 缺 name 字段应返回 422。"""
    response = auth_client.post("/api/kbs", json={"description": "缺名称"})
    assert response.status_code == 422


@patch("src.api.knowledge_base._get_service")
def test_delete_kb_exists(mock_get_service, auth_client):
    """POST /api/kbs/delete 删除已存在的知识库。"""
    mock_svc = mock_get_service.return_value
    mock_svc.delete_knowledge_base = AsyncMock(return_value=(True, "知识库已删除"))

    response = auth_client.post("/api/kbs/delete", json={"kb_id": "kb-1"})

    assert response.status_code == 200


@patch("src.api.knowledge_base._get_service")
def test_delete_kb_not_found(mock_get_service, auth_client):
    """POST /api/kbs/delete 不存在的知识库返回 404。"""
    mock_svc = mock_get_service.return_value
    mock_svc.delete_knowledge_base = AsyncMock(return_value=(False, "知识库不存在"))

    response = auth_client.post("/api/kbs/delete", json={"kb_id": "kb-missing"})

    assert response.status_code == 404
```

- [ ] **Step 1: 替换 test_knowledge_base.py** — 用上述代码替换
- [ ] **Step 2: 运行验证**

Run: `python -m pytest tests/api/test_knowledge_base.py -v`
Expected: 5 passed

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_knowledge_base.py
git commit -m "test(api): fix patch paths and add 422 validation for KB endpoints"
```

---

### Task 4: 修复并扩充 test_documents.py

**Files:**
- Modify: `tests/api/test_documents.py`

完整替换文件内容：
```python
"""文档 API 端点测试 — list / upload / status / chunks / delete。"""

from unittest.mock import AsyncMock, MagicMock, patch

from tests.api.mock_data import make_doc, make_chunk


@patch("src.api.documents._get_service")
def test_get_documents(mock_get_service, auth_client):
    """POST /api/kbs/documents/list 返回文档列表。"""
    mock_svc = mock_get_service.return_value
    mock_svc.get_documents = AsyncMock(return_value=[make_doc("doc-1", "report.pdf")])

    response = auth_client.post("/api/kbs/documents/list", json={"kb_id": "kb-1"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["filename"] == "report.pdf"


@patch("src.api.documents.asyncio.create_task", new_callable=MagicMock)
@patch("src.api.documents.FileStore")
@patch("src.api.documents._get_service")
def test_upload_document(mock_get_service, mock_file_store_cls, mock_create_task, auth_client):
    """POST /api/kbs/documents/upload 返回 202 Accepted。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db = MagicMock()
    mock_svc.db.get_documents = AsyncMock(return_value=[])
    mock_svc.db.add_document = AsyncMock(return_value="test-doc-uuid")

    mock_file_store_cls.build_path.return_value = "test/path.pdf"
    mock_fs = MagicMock()
    mock_fs.upload.return_value = True
    mock_file_store_cls.return_value = mock_fs

    response = auth_client.post(
        "/api/kbs/documents/upload",
        data={"kb_id": "kb-1"},
        files={"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")},
    )

    assert response.status_code == 202


@patch("src.api.documents._get_service")
def test_document_status_processing(mock_get_service, auth_client):
    """POST /api/kbs/documents/status 返回文档处理状态。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_documents = AsyncMock(return_value=[
        make_doc("doc-1", status="processing",
                 processing_progress=30, processing_state="extracting",
                 processing_message="正在解析..."),
    ])

    response = auth_client.post(
        "/api/kbs/documents/status", json={"kb_id": "kb-1", "doc_id": "doc-1"}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "processing"
    assert data["progress"] == 30


@patch("src.api.documents._get_service")
def test_document_status_not_found(mock_get_service, auth_client):
    """POST /api/kbs/documents/status 文档不存在返回 status=not_found。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_documents = AsyncMock(return_value=[])

    response = auth_client.post(
        "/api/kbs/documents/status", json={"kb_id": "kb-1", "doc_id": "missing"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "not_found"


@patch("src.api.documents._get_service")
def test_document_chunks_empty(mock_get_service, auth_client):
    """POST /api/kbs/documents/chunks 空文档返回空列表。"""
    mock_svc = mock_get_service.return_value
    mock_vs = MagicMock()
    mock_vs.get_chunks_paginated.return_value = {
        "items": [], "total": 0, "page": 1, "page_size": 10,
    }
    mock_svc.vector_store = mock_vs

    response = auth_client.post("/api/kbs/documents/chunks", json={
        "kb_id": "kb-1", "doc_id": "doc-1", "page": 1, "page_size": 10,
    })

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 0


@patch("src.api.documents._get_service")
def test_document_chunks_with_parent_dedup(mock_get_service, auth_client):
    """POST /api/kbs/documents/chunks parent_content 去重逻辑验证。"""
    mock_svc = mock_get_service.return_value
    mock_vs = MagicMock()
    mock_vs.get_chunks_paginated.return_value = {
        "items": [
            make_chunk("c1", "2024年营收100亿", page=1, parent_content="营收概述"),
            make_chunk("c2", "2024年净利润20亿", page=1, parent_content="营收概述"),
            make_chunk("c3", "毛利率45%", page=2, parent_content="财务指标"),
        ],
        "total": 3, "page": 1, "page_size": 10,
    }
    mock_svc.vector_store = mock_vs

    response = auth_client.post("/api/kbs/documents/chunks", json={
        "kb_id": "kb-1", "doc_id": "doc-1", "page": 1, "page_size": 10,
    })

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 3
    assert data["page"] == 1
    assert len(data["items"]) == 3
    assert data["items"][0]["parent_key"] == "p0"
    assert data["items"][1]["parent_key"] == "p0"
    assert data["items"][2]["parent_key"] == "p1"
    assert data["items"][0].get("parent_content") is None
    assert data["parent_map"]["p0"] == "营收概述"
    assert data["parent_map"]["p1"] == "财务指标"
    assert len(data["parent_map"]) == 2


@patch("src.api.documents._get_service")
def test_delete_document_success(mock_get_service, auth_client):
    """POST /api/kbs/documents/delete 成功返回 success=True。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.soft_delete_document = AsyncMock(return_value=True)
    mock_svc.vector_store = MagicMock()

    response = auth_client.post(
        "/api/kbs/documents/delete", json={"kb_id": "kb-1", "doc_id": "doc-1"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["success"] is True


@patch("src.api.documents._get_service")
def test_delete_document_not_found(mock_get_service, auth_client):
    """POST /api/kbs/documents/delete 文档不存在返回 success=False。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.soft_delete_document = AsyncMock(return_value=False)

    response = auth_client.post(
        "/api/kbs/documents/delete", json={"kb_id": "kb-1", "doc_id": "missing"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["success"] is False
```

- [ ] **Step 1: 替换 test_documents.py**
- [ ] **Step 2: 运行验证**

Run: `python -m pytest tests/api/test_documents.py -v`
Expected: 9 passed

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_documents.py
git commit -m "test(api): fix patch paths and add status/chunks/delete tests"
```

---

### Task 5: 修复 test_chat.py — mock 真实方法

**Files:**
- Modify: `tests/api/test_chat.py`

完整替换：
```python
"""Tests for SSE streaming chat endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from tests.api.mock_data import make_chunk

from src.main import app

client = TestClient(app)


@patch("src.api.chat._get_service")
def test_chat_stream_returns_sse(mock_get_service):
    """GET /api/chat/stream returns SSE event stream."""
    mock_svc = mock_get_service.return_value
    mock_chain = mock_svc.rag_chain

    async def fake_search(query, kb_id):
        return [make_chunk("1", "test", page=1)]

    def fake_stream(query, contexts, history, trace_id=None):
        yield "净利润"
        yield "为"
        yield "100亿"
        yield "元"

    mock_chain.search = fake_search
    mock_chain.rerank = MagicMock(return_value=[])
    mock_chain.stream_answer = fake_stream

    response = client.get(
        "/api/chat/stream?session_id=s1&kb_id=kb-1&query=净利润多少"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
```

- [ ] **Step 1: 替换 test_chat.py**
- [ ] **Step 2: 运行验证**

Run: `python -m pytest tests/api/test_chat.py -v`
Expected: 1 passed

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_chat.py
git commit -m "test(api): fix chat mock to use real chain methods"
```

---

### Task 6: 新建 test_auth.py

**Files:**
- Create: `tests/api/test_auth.py`

```python
"""Auth 端点测试 — login / verify / logout / anonymous。"""

from unittest.mock import AsyncMock, MagicMock, patch

from tests.api.mock_data import make_user


# ─── Login ───

@patch("src.api.auth.UserAuth.hash_password", return_value="hashed_pwd")
@patch("src.api.auth._get_service")
def test_login_new_user_auto_register(mock_get_service, mock_hash, client):
    """新用户自动注册并返回 token。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_user_by_account = AsyncMock(return_value=None)
    mock_svc.db.add_user = AsyncMock()
    mock_svc.db.update_user_token = AsyncMock()
    mock_svc.redis_client = MagicMock()

    with patch("src.api.auth.UserAuth.generate_token", return_value="test-token"):
        with patch(
            "src.api.auth.UserAuth.store_token_async", new_callable=AsyncMock
        ) as mock_store:
            response = client.post("/api/auth/login", json={
                "account": "newuser", "password": "pass123"
            })

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["token"] == "test-token"
    assert len(data["user_id"]) > 0


@patch("src.api.auth.UserAuth.hash_password", return_value="correct_hash")
@patch("src.api.auth._get_service")
def test_login_existing_user_correct_password(mock_get_service, mock_hash, client):
    """已有用户，密码正确，返回 token。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_user_by_account = AsyncMock(
        return_value=make_user("u1", "existing", "correct_hash")
    )
    mock_svc.db.update_user_token = AsyncMock()
    mock_svc.redis_client = MagicMock()

    with patch("src.api.auth.UserAuth.generate_token", return_value="test-token"):
        with patch(
            "src.api.auth.UserAuth.store_token_async", new_callable=AsyncMock
        ) as mock_store:
            response = client.post("/api/auth/login", json={
                "account": "existing", "password": "pass123"
            })

    assert response.status_code == 200
    assert response.json()["data"]["token"] == "test-token"


@patch("src.api.auth.UserAuth.hash_password", return_value="wrong_hash")
@patch("src.api.auth._get_service")
def test_login_wrong_password(mock_get_service, mock_hash, client):
    """密码错误返回 401。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_user_by_account = AsyncMock(
        return_value=make_user("u1", "existing", "correct_hash")
    )

    response = client.post("/api/auth/login", json={
        "account": "existing", "password": "wrong"
    })

    assert response.status_code == 401


def test_login_missing_password(client):
    """缺 password 字段返回 422。"""
    response = client.post("/api/auth/login", json={"account": "test"})
    assert response.status_code == 422


# ─── Verify ───

@patch("src.api.auth.UserAuth.get_user_id_from_token_async",
       new_callable=AsyncMock, return_value="u1")
@patch("src.api.auth._get_service")
def test_verify_token_valid(mock_get_service, mock_get_uid, client):
    """有效 token 返回 valid=True + user_id。"""
    client.cookies.set("token", "valid-token")
    response = client.post("/api/auth/verify")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    assert data["user_id"] == "u1"


@patch("src.api.auth._get_service")
def test_verify_no_token(mock_get_service, client):
    """无 Cookie 时返回 valid=False。"""
    response = client.post("/api/auth/verify")
    assert response.status_code == 200
    assert response.json()["data"]["valid"] is False


# ─── Logout ───

@patch("src.api.auth.UserAuth.delete_token_async", new_callable=AsyncMock)
@patch("src.api.auth._get_service")
def test_logout(mock_get_service, mock_delete, client):
    """退出登录清除 token。"""
    client.cookies.set("token", "test-token")
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    assert response.json()["data"]["message"] == "已退出登录"


# ─── Anonymous ───

def test_anonymous_new_user(client):
    """无 Cookie 时生成新匿名 ID。"""
    response = client.post("/api/auth/anonymous")
    assert response.status_code == 200
    assert len(response.json()["data"]["user_id"]) == 36


def test_anonymous_existing_user(client):
    """已有匿名 Cookie 时返回已有 ID。"""
    client.cookies.set("user_id", "fixed-uuid-0000-0000")
    response = client.post("/api/auth/anonymous")
    assert response.status_code == 200
    assert response.json()["data"]["user_id"] == "fixed-uuid-0000-0000"
```

- [ ] **Step 1: Write `tests/api/test_auth.py`**
- [ ] **Step 2: 运行验证**

Run: `python -m pytest tests/api/test_auth.py -v`
Expected: 9 passed

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_auth.py
git commit -m "test(api): add auth endpoint tests"
```

---

### Task 7: 新建 test_sessions.py

**Files:**
- Create: `tests/api/test_sessions.py`

```python
"""Sessions 端点测试 — list / messages / delete。"""

from unittest.mock import AsyncMock, MagicMock, patch

from tests.api.mock_data import make_session, make_message


@patch("src.api.sessions._get_service")
def test_list_sessions(mock_get_service, auth_client):
    """POST /api/sessions/list 返回会话列表。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_sessions = AsyncMock(return_value=[
        make_session("s1", "财报问答"),
        make_session("s2", "年报分析"),
    ])

    response = auth_client.post("/api/sessions/list", json={})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["title"] == "财报问答"


@patch("src.api.sessions._get_service")
def test_list_sessions_empty(mock_get_service, auth_client):
    """POST /api/sessions/list 无会话返回空列表。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_sessions = AsyncMock(return_value=[])

    response = auth_client.post("/api/sessions/list", json={})

    assert response.status_code == 200
    assert response.json()["data"] == []


@patch("src.api.sessions._get_service")
def test_session_messages(mock_get_service, auth_client):
    """POST /api/sessions/messages 返回消息列表。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_session_by_id = AsyncMock(return_value=make_session("s1"))
    mock_svc.db.get_messages = AsyncMock(return_value=[
        make_message("user", "2024年营收多少"),
        make_message("assistant", "2024年营收为100亿"),
    ])

    response = auth_client.post("/api/sessions/messages", json={"session_id": "s1"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["role"] == "user"
    assert data[1]["role"] == "assistant"


@patch("src.api.sessions._get_service")
def test_session_messages_not_found(mock_get_service, auth_client):
    """POST /api/sessions/messages session 不存在返回 404。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_session_by_id = AsyncMock(return_value=None)

    response = auth_client.post("/api/sessions/messages", json={"session_id": "missing"})

    assert response.status_code == 404


@patch("src.api.sessions._get_service")
def test_delete_session(mock_get_service, auth_client):
    """POST /api/sessions/delete 删除成功。"""
    mock_svc = mock_get_service.return_value
    mock_svc.rag_chain.chat_manager.cleanup_session = MagicMock()
    mock_svc.db.delete_session_and_messages = AsyncMock(return_value=True)

    response = auth_client.post("/api/sessions/delete", json={"session_id": "s1"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["success"] is True


@patch("src.api.sessions._get_service")
def test_delete_session_not_found(mock_get_service, auth_client):
    """POST /api/sessions/delete session 不存在返回 404。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.delete_session_and_messages = AsyncMock(return_value=False)

    response = auth_client.post("/api/sessions/delete", json={"session_id": "missing"})

    assert response.status_code == 404
```

- [ ] **Step 1: Write `tests/api/test_sessions.py`**
- [ ] **Step 2: 运行验证**

Run: `python -m pytest tests/api/test_sessions.py -v`
Expected: 6 passed

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_sessions.py
git commit -m "test(api): add session endpoint tests"
```

---

### Task 8: 新建 test_kb_eval.py

**Files:**
- Create: `tests/api/test_kb_eval.py`

```python
"""KB 评估端点测试 — eval/latest。"""

from unittest.mock import AsyncMock, patch

from tests.api.mock_data import make_eval_report


@patch("src.api.kb_eval._get_service")
def test_latest_eval_found(mock_get_service, auth_client):
    """POST /api/kbs/eval/latest 返回评估报告。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_latest_eval_report = AsyncMock(
        return_value=make_eval_report(0.84, passed=True, qa_count=20)
    )

    response = auth_client.post("/api/kbs/eval/latest", json={"kb_id": "kb-1"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["overall_score"] == 0.84
    assert data["passed"] is True
    assert data["qa_count"] == 20


@patch("src.api.kb_eval._get_service")
def test_latest_eval_not_found(mock_get_service, auth_client):
    """POST /api/kbs/eval/latest 无评估报告返回 data=None。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_latest_eval_report = AsyncMock(return_value=None)

    response = auth_client.post("/api/kbs/eval/latest", json={"kb_id": "kb-no-eval"})

    assert response.status_code == 200
    assert response.json()["data"] is None
```

- [ ] **Step 1: Write `tests/api/test_kb_eval.py`**
- [ ] **Step 2: 运行验证**

Run: `python -m pytest tests/api/test_kb_eval.py -v`
Expected: 2 passed

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_kb_eval.py
git commit -m "test(api): add KB eval endpoint tests"
```

---

### Task 9: 补 test_health.py — 添加 config 测试

**Files:**
- Modify: `tests/api/test_health.py`

```python
"""Tests for health check and config endpoints."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def test_health_returns_200():
    """GET /api/health returns 200 with status ok."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch("src.api.health._get_service")
def test_app_config_returns_max_size(mock_get_service):
    """POST /api/config 返回上传大小限制。"""
    mock_svc = mock_get_service.return_value
    mock_svc.get_max_upload_size = AsyncMock(return_value=10485760)

    response = client.post("/api/config")

    assert response.status_code == 200
    assert response.json()["data"]["max_upload_size"] == 10485760
```

- [ ] **Step 1: 替换 test_health.py**
- [ ] **Step 2: 运行验证**

Run: `python -m pytest tests/api/test_health.py -v`
Expected: 2 passed

- [ ] **Step 3: Commit**

```bash
git add tests/api/test_health.py
git commit -m "test(api): add config endpoint test"
```

---

### Task 10: 最终验证

- [ ] **Step 1: 运行全部 API 测试**

Run: `python -m pytest tests/api/ -v`
Expected: 全部通过（约 34 个测试）

- [ ] **Step 2: 确认无旧路径残留**

Run: `grep -rn "src.api.routes" tests/api/`
Expected: 无输出

- [ ] **Step 3: 最终 commit**

```bash
git add tests/api/
git commit -m "test(api): complete API unit test suite"
```
