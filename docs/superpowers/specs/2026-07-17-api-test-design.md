# API 单元测试补齐方案设计

## 背景

代码重构后 API 文件路径从 `src/api/routes/*` 改为 `src/api/*`，但测试文件中的 `@patch` 路径未同步更新，导致：

1. 6 个现有 API 测试全部因 `AttributeError: module 'src.api' has no attribute 'routes'` 失败
2. `test_chat.py` 虽通过但 mock 了 `RAGChain` 上不存在的方法（`chat_with_citations`），实际路径是 `search`/`rerank`/`stream_answer`
3. 19 个端点中 11 个完全没有测试覆盖

这些问题导致"测试通过但接口实际是坏的"——测试不能真实拦截回归。

## 目标

1. 修复 6 个路径错误的现有测试，使其能真实验证接口
2. 补齐关键路径端点（auth、sessions、kb_eval、documents 子端点）的单元测试
3. 提取公共测试基设施到 `conftest.py`，减少重复
4. 添加 `make test-api` 命令，支持专项运行 API 测试

## 目录结构

```
tests/api/
├── conftest.py              # 🆕 TestClient + auth_client fixture
├── test_health.py           # ✅ 已有，保留
├── test_background_task.py  # ✅ 已有，保留
├── test_chat.py             # 🔧 修 mock 路径
├── test_knowledge_base.py   # 🔧 修 patch 路径 + 补响应校验
├── test_documents.py        # 🔧 修 patch 路径 + 补新端点测试
├── test_auth.py             # 🆕 login / verify / logout
├── test_sessions.py         # 🆕 list / messages / delete
└── test_kb_eval.py          # 🆕 eval/latest
```

## conftest.py — 公共测试基设施

```python
"""API 测试公共基设施 — TestClient + auth 辅助函数。"""

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
    """返回带认证 Cookie 的 TestClient，自动绕过中间件 token 校验。"""
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

`auth_client` 适用于所有需要登录态的端点（kb、documents、sessions）。
`client` 适用于无需登录态的端点（health、auth）。

## 各文件修改方案

### 1. test_knowledge_base.py — 修 patch 路径

**修复：** 所有 `@patch("src.api.routes.knowledge_base._get_service")` 改为 `@patch("src.api.knowledge_base._get_service")`（4 处）

**补校验：** 现有测试只 assert status_code，补 body 结构校验：

```python
def test_list_kbs(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_svc.list_knowledge_bases = AsyncMock(return_value=[
        {"id": "kb-1", "name": "年报知识库", "doc_count": 5},
    ])
    resp = auth_client.post("/api/kbs/list", json={})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["name"] == "年报知识库"
```

新增一个 422 场景：
```python
def test_create_kb_missing_name(mock_get_service, auth_client):
    resp = auth_client.post("/api/kbs", json={"description": "缺名称"})
    assert resp.status_code == 422
```

**文件结构：** 改为使用 `auth_client` fixture 替代手动 `_setup_auth()`。删除 `_setup_auth()` 函数。

### 2. test_documents.py — 修 patch 路径 + 补新端点

**修复：** `@patch("src.api.routes.documents._get_service")` → `@patch("src.api.documents._get_service")`（2 处）
`@patch("src.api.routes.documents.FileStore")` → `@patch("src.api.documents.FileStore")`（1 处）

**新增 5 个测试：**

```python
# 文档状态轮询 — 正常
def test_document_status_processing(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_documents = AsyncMock(return_value=[{"id": "doc-1", "status": "processing", ...}])
    resp = auth_client.post("/api/kbs/documents/status", json={"kb_id": "kb-1", "doc_id": "doc-1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "processing"

# 文档状态轮询 — 不存在
def test_document_status_not_found(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_documents = AsyncMock(return_value=[])
    resp = auth_client.post("/api/kbs/documents/status", json={"kb_id": "kb-1", "doc_id": "missing"})
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "not_found"

# 分块预览
def test_document_chunks(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_vs = MagicMock()
    mock_vs.get_chunks_paginated.return_value = {"items": [], "total": 0, "page": 1, "page_size": 10}
    mock_svc.vector_store = mock_vs
    resp = auth_client.post("/api/kbs/documents/chunks", json={"kb_id": "kb-1", "doc_id": "doc-1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 0

# 文档删除 — 成功
def test_delete_document_success(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_svc.db.soft_delete_document = AsyncMock(return_value=True)
    resp = auth_client.post("/api/kbs/documents/delete", json={"kb_id": "kb-1", "doc_id": "doc-1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is True

# 文档删除 — 不存在
def test_delete_document_not_found(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_svc.db.soft_delete_document = AsyncMock(return_value=False)
    resp = auth_client.post("/api/kbs/documents/delete", json={"kb_id": "kb-1", "doc_id": "missing"})
    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is False
```

### 3. test_chat.py — 修 mock 方法

**当前问题：** mock 了 `chain.chat_with_citations`，但实际 `_stream_rag_response` 调用的是 `chain.search`、`chain.rerank`、`chain.stream_answer`。

**修复方案：**

```python
@patch("src.api.chat._get_service")
def test_chat_stream_returns_sse(mock_get_service):
    mock_svc = mock_get_service.return_value
    mock_chain = mock_svc.rag_chain

    async def fake_search(query, kb_id):
        return [{"id": "1", "content": "test", "metadata": {"source": "a.pdf", "page": 1, "doc_id": "d1"}}]

    def fake_stream(query, contexts, history, trace_id=None):
        yield "净利润"
        yield "为100亿"
        yield "元"

    mock_chain.search = fake_search
    mock_chain.rerank = MagicMock(return_value=[])
    mock_chain.stream_answer = fake_stream

    response = client.get("/api/chat/stream?session_id=s1&kb_id=kb-1&query=净利润多少")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
```

注意：SSE 的 `StreamingResponse` 是惰性的，测试不消费生成器，只验证状态码和 content-type。这是 TestClient 的限制，后续可考虑用 `response.iter_lines()` 消费 SSE 事件。

### 4. test_auth.py — 🆕 新建

```python
"""Auth 端点测试 — login / verify / logout。"""

# login — 新用户自动注册
def test_login_new_user_auto_register(mock_get_service, client):
    # mock svc.db.get_user_by_account return None
    # 验证: 返回 200 + token + user_id

# login — 已有用户，密码正确
def test_login_existing_user(mock_get_service, client):
    # mock svc.db.get_user_by_account 返回 {"id": "u1", "password": hash}
    # mock UserAuth.hash_password 返回相同 hash
    # 验证: 返回 200 + token

# login — 密码错误
def test_login_wrong_password(mock_get_service, client):
    # mock svc.db.get_user_by_account 返回 {"id": "u1", "password": other_hash}
    # 验证: 返回 401

# login — 缺密码（422）
def test_login_missing_password(client):
    resp = client.post("/api/auth/login", json={"account": "test"})
    assert resp.status_code == 422

# verify — token 有效
def test_verify_token_valid(mock_get_service, client):
    # mock 需要绕过 UserAuth.get_user_id_from_token_async
    # 这个函数既在中间件被调用，也在 verify_token 中被调用
    # 方案：patch "src.api.auth.UserAuth.get_user_id_from_token_async"
    # 验证: 返回 200 + valid=True

# verify — 无 Cookie
def test_verify_no_token(client):
    resp = client.post("/api/auth/verify")
    assert resp.status_code == 200
    assert resp.json()["data"]["valid"] is False

# logout — 有 token
def test_logout(mock_get_service, client):
    # 在 Cookie 中设置 token
    # mock UserAuth.delete_token_async
    # 验证: 返回 200 + "已退出登录"

# anonymous — 新用户
def test_anonymous_new_user(client):
    resp = client.post("/api/auth/anonymous")
    assert resp.status_code == 200
    assert "user_id" in resp.json()["data"]
```

注意：auth 端点不需要 auth_client fixture（它们不在中间件保护范围内）。

auth 端点的 mock 依赖于 `_get_service` 路径：`src.api.auth._get_service`。

对于 `verify_token` 端点，它调用 `UserAuth.get_user_id_from_token_async(svc.redis_client, token)`，需要 patch 这个函数而非中间件版本。patch 路径是 `src.api.auth.UserAuth.get_user_id_from_token_async`。

### 5. test_sessions.py — 🆕 新建

```python
"""Sessions 端点测试 — list / messages / delete。"""

# list_sessions — 有会话
def test_list_sessions(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_sessions = AsyncMock(return_value=[
        {"id": "s1", "title": "财报问答", "kb_id": "kb-1", "kb_name": "年报",
         "message_count": 3, "created_at": datetime(2026,1,1), "updated_at": datetime(2026,1,2)},
    ])
    resp = auth_client.post("/api/sessions/list", json={})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["title"] == "财报问答"

# list_sessions — 空列表
def test_list_sessions_empty(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_sessions = AsyncMock(return_value=[])
    resp = auth_client.post("/api/sessions/list", json={})
    assert resp.status_code == 200
    assert resp.json()["data"] == []

# get_session_messages — 成功
def test_session_messages(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_session_by_id = AsyncMock(return_value={"id": "s1", "title": "test"})
    mock_svc.db.get_messages = AsyncMock(return_value=[
        {"role": "user", "content": "hello", "sources": None, "created_at": datetime(2026,1,1)},
    ])
    resp = auth_client.post("/api/sessions/messages", json={"session_id": "s1"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["role"] == "user"

# get_session_messages — session 不存在
def test_session_messages_not_found(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_session_by_id = AsyncMock(return_value=None)
    resp = auth_client.post("/api/sessions/messages", json={"session_id": "missing"})
    assert resp.status_code == 404

# delete_session — 成功
def test_delete_session(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_svc.rag_chain.chat_manager.cleanup_session = MagicMock()
    mock_svc.db.delete_session_and_messages = AsyncMock(return_value=True)
    resp = auth_client.post("/api/sessions/delete", json={"session_id": "s1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is True

# delete_session — 不存在
def test_delete_session_not_found(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_svc.db.delete_session_and_messages = AsyncMock(return_value=False)
    resp = auth_client.post("/api/sessions/delete", json={"session_id": "missing"})
    assert resp.status_code == 404
```

### 6. test_kb_eval.py — 🆕 新建

```python
"""KB 评估端点测试 — eval/latest。"""

def test_latest_eval_found(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_latest_eval_report = AsyncMock(return_value={
        "eval_date": datetime(2026, 6, 15),
        "faithfulness": Decimal("0.85"),
        "answer_relevancy": Decimal("0.90"),
        "context_precision": Decimal("0.78"),
        "context_recall": Decimal("0.82"),
        "overall_score": Decimal("0.84"),
        "passed": True,
        "qa_count": 20,
        "run_type": "full",
    })
    resp = auth_client.post("/api/kbs/eval/latest", json={"kb_id": "kb-1"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["overall_score"] == 0.84
    assert data["passed"] is True
    assert data["qa_count"] == 20

def test_latest_eval_not_found(mock_get_service, auth_client):
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_latest_eval_report = AsyncMock(return_value=None)
    resp = auth_client.post("/api/kbs/eval/latest", json={"kb_id": "kb-no-eval"})
    assert resp.status_code == 200
    assert resp.json()["data"] is None
```

## 一个特殊问题：patch 路径冲突

`UserAuth.get_user_id_from_token_async` 在两个地方被调用：
1. `src/middleware/auth.py` — 中间件校验 token 时
2. `src/api/auth.py:73` — verify_token 端点中

`auth_client` fixture 已经 patch 了中间件版本（`src.middleware.auth.UserAuth.get_user_id_from_token_async`）。但 verify 端点在测试中调用的是 `src.api.auth.UserAuth.get_user_id_from_token_async`。

**解决方案：** `test_auth.py` 中不依赖 `auth_client`，而是直接在测试中 patch 需要的路径：

```python
@patch("src.api.auth.UserAuth.get_user_id_from_token_async", new_callable=AsyncMock, return_value="u1")
@patch("src.api.auth._get_service")
def test_verify_token_valid(mock_get_service, mock_get_uid, client):
    ...
```

## 运行方式

添加 Makefile target：

```makefile
# pyproject.toml 中添加
[tool.pytest.ini_options]
markers = [
    "api: API route integration tests",
]
```

运行命令：

```bash
# 全部 API 测试
python -m pytest tests/api/ -v

# 带覆盖率
python -m pytest tests/api/ --cov=src.api

# 加 -x 首次失败即停止，快速迭代
python -m pytest tests/api/ -x -v
```

## 验收标准

1. `python -m pytest tests/api/ -v` — 所有 20+ 测试通过
2. 测试覆盖到每个端点的 Happy path + 至少一个错误场景
3. `conftest.py` 无冗余代码
4. 每个测试都能真实拦截接口回归（不 mock 不存在的方法，不用错误的 patch 路径）
