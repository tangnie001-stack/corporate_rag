# POST 标准化 + Trace ID 全链路 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一所有 API 为 POST 方法（SSE/Health 除外），引入全链路 trace ID 从前端到 Langfuse，`user_id` 通过 contextvar 传递。

**Architecture:** 分为两层改造：POST 标准化（后端路由 + 前端调用）和 Trace ID 链路（中间件 + contextvar + Langfuse）。两条线互不依赖，可并行。

**Tech Stack:** Python 3.11+ / FastAPI / Langfuse SDK / loguru / 原生 JS + fetch + EventSource

## Global Constraints

- `contextvars` 用于 per-request 上下文传递，不通过 request 参数传参
- 除 `/api/health` 和 `/api/chat/stream` 外，所有端点改为 POST
- 列表接口统一 `List` 后缀，删除接口统一 `Delete` 后缀，ID 放 body
- 每个带参数的端点使用独立的 Pydantic RequestBody/Response class
- 前端 JS 文件版本号在 HTML 中通过 `?v=N` 管理，修改后手动 +1
- 所有 git 操作手动执行，不自动 commit/push

---
## 文件结构

### 新增文件
| 文件 | 职责 |
|------|------|
| `src/infra/llm/trace_context.py` | 定义 `current_trace_id`、`current_user_id` 两个 contextvar |
| `src/middleware/trace_id.py` | TraceID 中间件：提取/生成 trace_id，设 contextvar，回写响应头 |

### 修改文件
| 文件 | 职责 |
|------|------|
| `src/api/main.py` | 注册 TraceID 中间件，配置 loguru filter |
| `src/middleware/auth.py` | +1 行设置 `current_user_id.set(uid)` |
| `src/infra/llm/langfuse_tracing.py` | `start_trace()` 自动读取 contextvar |
| `src/api/routes/auth.py` | login→JSON body, verify/anonymous→POST |
| `src/api/routes/knowledge_base.py` | list/delete 拆分路径，kb_id 移入 body，新增 Pydantic models |
| `src/api/routes/documents.py` | 4 个路由路径/参数重构，新增 Pydantic models |
| `src/api/routes/sessions.py` | list/messages/delete 拆分路径，sid 移入 body，新增 Pydantic models |
| `nginx/html/js/api.js` | 生成 trace_id 注入 X-Trace-ID，所有调用按新路径/方法/body 更新 |
| `nginx/html/js/chat.js` | SSE URL 追加 `&trace_id=xxx` |
| `nginx/html/login.html` | login fetch 改为 JSON body |
| `nginx/html/index.html` | status/chunks 直接 fetch 改为 POST+body |
| `nginx/html/chat.html` | verify 直接 fetch 改为 POST |
| `tests/api/test_knowledge_base.py` | 3 个测试方法/路径/body |
| `tests/api/test_documents.py` | 2 个测试方法/路径/body |
| `docs/api-contract.md` | 端点定义全面更新 |
| `docs/api_contract.md` | 端点定义全面更新 |
| `src/api/README.md` | 端点描述同步更新 |
| `src/chat_manager.py` | docstring 更新 |

---

### Task 1: 创建 trace_context.py

**Files:**
- Create: `src/infra/llm/trace_context.py`
- Test: 无单独测试（集成验证在 Task 5）

**Interfaces:**
- Produces: `current_trace_id: ContextVar[str | None]`, `current_user_id: ContextVar[str]`

- [ ] **Step 1: 创建文件**

```python
"""请求级上下文变量 — 通过 contextvars 实现 per-request 数据传递。

提供 current_trace_id 和 current_user_id 两个 ContextVar，
分别在 TraceID 中间件和 Auth 中间件中设置，供 LangfuseTracer、
日志过滤等下游模块自动读取，无需显式传参。
"""

from contextvars import ContextVar

current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)
current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
```

---

### Task 2: 创建 TraceID 中间件

**Files:**
- Create: `src/middleware/trace_id.py`
- Modify: 后续 Task 3 在 main.py 注册

**Interfaces:**
- Produces: `trace_id_middleware` — 函数式中间件，设置 `request.state.trace_id` + contextvar，回写响应头

- [ ] **Step 1: 创建中间件文件**

```python
"""TraceID 中间件 — 在请求入口生成/提取 trace_id，注入全链路。

优先级: X-Trace-ID 请求头 → ?trace_id 查询参数 → uuid4 自动生成。
响应头 X-Trace-ID 统一回传，覆盖正常/异常/SSE 全部场景。
"""

import uuid

from fastapi import Request, Response
from loguru import logger

from src.infra.llm.trace_context import current_trace_id


async def trace_id_middleware(request: Request, call_next):
    # 1. 获取 trace_id：header → query → auto-generate
    trace_id = request.headers.get("X-Trace-ID")
    if not trace_id:
        trace_id = request.query_params.get("trace_id")
    if not trace_id:
        trace_id = str(uuid.uuid4())

    # 2. 注入 request.state 和 contextvar
    request.state.trace_id = trace_id
    current_trace_id.set(trace_id)

    # 3. 继续处理请求
    response: Response = await call_next(request)

    # 4. 回写响应头（覆盖所有返回路径）
    response.headers["X-Trace-ID"] = trace_id
    return response
```

---

### Task 3: 配置 loguru + 注册中间件

**Files:**
- Modify: `src/api/main.py`

- [ ] **Step 1: 在 main.py 添加 loguru 配置和中间件注册**

在当前 `src/api/main.py` 的 `app = FastAPI(...)` 之后，`app.add_middleware(CORSMiddleware, ...)` 之前，添加 loguru filter：

```python
# 配置 loguru 自动注入 trace_id
from src.infra.llm.trace_context import current_trace_id as _trace_var


def _trace_id_filter(record):
    record["extra"]["trace_id"] = _trace_var.get() or ""
    return True


logger.configure(extra={"trace_id": ""}, filter=_trace_id_filter)
```

然后在 `app.add_middleware(CORSMiddleware, ...)` 之后，`app.add_middleware(ResponseEnvelopeMiddleware)` 之前，注册 TraceID 中间件：

```python
from src.middleware.trace_id import trace_id_middleware

# 中间件注册顺序（请求从外到内）：
# CORS → TraceID → ResponseEnvelope → auth → router
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(trace_id_middleware)   # ← 新增，放在 CORS 之后

app.add_middleware(ResponseEnvelopeMiddleware)
app.middleware("http")(auth_middleware)
```

---

### Task 4: LangfuseTracer 读取 contextvar

**Files:**
- Modify: `src/infra/llm/langfuse_tracing.py`

- [ ] **Step 1: 修改 start_trace 读取 contextvar**

```python
# 在 langfuse_tracing.py 文件头部新增导入
from src.infra.llm.trace_context import current_trace_id

# 修改 start_trace 方法 (第 58-75 行):
def start_trace(
    self, name: str, input_data: Any = None, session_id: Optional[str] = None
) -> Optional[str]:
    if not self._initialized:
        return None
    ext_id = current_trace_id.get()
    kwargs = dict(name=name, input=input_data, session_id=session_id)
    if ext_id:
        kwargs["id"] = ext_id  # Langfuse SDK 显式接受 id 参数
    return self._client.trace(**kwargs).id
```

---

### Task 5: Auth 中间件同步设置 contextvar

**Files:**
- Modify: `src/middleware/auth.py`

- [ ] **Step 1: 在 3 处 `request.state.user_id = uid` 后同步设 contextvar**

在文件头部添加导入:

```python
from src.infra.llm.trace_context import current_user_id
```

在第 49 行、60 行、65 行的 `request.state.user_id = uid` 后各加一行：

```python
request.state.user_id = uid
current_user_id.set(uid)         # ← 新增
```

共改动 3 处。

---

### Task 6: 重构 auth.py 路由

**Files:**
- Modify: `src/api/routes/auth.py`

- [ ] **Step 1: 添加 Pydantic 模型，修改路由装饰器**

```python
"""认证端点 — login/verify/logout/anonymous。"""

import uuid
from fastapi import APIRouter, Cookie
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from src.app_service import AppService
from src.config.response_codes import Code
from src.infra.api_error import ApiError
from src.infra.auth.user_auth import UserAuth

router = APIRouter()

_service: AppService | None = None


def _get_service() -> AppService:
    global _service
    if _service is None:
        _service = AppService()
    return _service


class LoginRequest(BaseModel):
    """登录请求体。"""
    account: str
    password: str


@router.post("/auth/login")
async def login(body: LoginRequest):
    svc = _get_service()
    pw_hash = UserAuth.hash_password(body.password)
    user = await svc.db.get_user_by_account(body.account)
    if user:
        if user["password"] != pw_hash:
            raise ApiError(Code.AUTH_WRONG_PASSWORD, Code.AUTH_WRONG_PASSWORD_MSG, 401)
        user_id = user["id"]
    else:
        user_id = str(uuid.uuid4())
        await svc.db.add_user(user_id, body.account, pw_hash)
        logger.info("New user registered: {}", body.account)
    token = UserAuth.generate_token()
    await UserAuth.store_token_async(svc.redis_client, token, user_id)
    await svc.db.update_user_token(user_id, token)
    return {"token": token, "user_id": user_id}


@router.post("/auth/verify")
async def verify_token(token: str = Cookie(None)):
    if not token:
        return {"valid": False}
    svc = _get_service()
    uid = await UserAuth.get_user_id_from_token_async(svc.redis_client, token)
    return {"user_id": uid, "valid": uid is not None}


@router.post("/auth/logout")
async def logout(token: str = Cookie(None)):
    if token:
        svc = _get_service()
        await UserAuth.delete_token_async(svc.redis_client, token)
    return JSONResponse({"message": "已退出登录"})


@router.post("/auth/anonymous")
async def get_anonymous_id(user_id: str = Cookie(None)):
    if not user_id:
        user_id = str(uuid.uuid4())
    resp = JSONResponse({"user_id": user_id})
    resp.set_cookie("user_id", user_id, max_age=31536000, path="/", samesite="lax")
    return resp
```

变更总结：
- `login`: 参数从 `Form` 改为 `LoginRequest` Pydantic model（JSON body）
- `verify`: `@router.get` → `@router.post`，逻辑完全不变
- `anonymous`: `@router.get` → `@router.post`，逻辑完全不变

---

### Task 7: 重构 knowledge_base.py

**Files:**
- Modify: `src/api/routes/knowledge_base.py`

- [ ] **Step 1: 重构路由和 Pydantic 模型**

保留 `CreateKBRequest` 和 `CreateKBResponse` 不变。新增：

```python
class KBDeleteRequest(BaseModel):
    """删除知识库请求体。"""
    kb_id: str
```

修改路由装饰器：

```python
@router.post("/kbs/list")
async def list_knowledge_bases(request: Request):
    """列出所有知识库。"""
    svc = _get_service()
    user_id = getattr(request.state, "user_id", "")
    kbs = await svc.list_knowledge_bases(user_id)
    return kbs


@router.post("/kbs", status_code=201)
# create_knowledge_base — 不变


@router.post("/kbs/delete")
async def delete_knowledge_base(body: KBDeleteRequest):
    """删除知识库及其向量数据。"""
    svc = _get_service()
    success, message = await svc.delete_knowledge_base(body.kb_id)
    if not success:
        raise ApiError(Code.KB_NOT_FOUND, Code.KB_NOT_FOUND_MSG, 404)
    return {"success": True, "message": message}
```

变更总结：
- `list`: `GET /api/kbs` → `POST /api/kbs/list`
- `create`: 完全不变
- `delete`: `DELETE /api/kbs/{kb_id}` → `POST /api/kbs/delete`，`kb_id` 从路径改 body

---

### Task 8: 重构 documents.py

**Files:**
- Modify: `src/api/routes/documents.py`

- [ ] **Step 1: 新增 Pydantic 模型，重构 4 个路由**

在文件头部添加：

```python
from pydantic import BaseModel
from fastapi import Form
```

新增 Pydantic models:

```python
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
```

重构 4 个路由装饰器（函数体逻辑尽量保持不变，只改参数来源）：

**文档列表：**
```python
@router.post("/kbs/documents/list")
async def get_documents(body: DocumentListRequest, request: Request = None):
    svc = _get_service()
    docs = await svc.get_documents(body.kb_id)
    return docs
```

**文档上传：**
```python
@router.post("/kbs/documents/upload", status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form(...),
    request: Request = None,
):
    # 函数体内 kb_id 直接从参数取，不再从路径参数取
    user_id = getattr(request.state, "user_id", "") if request else ""
    # ...其余逻辑与当前一致...
```

**文档状态：**
```python
@router.post("/kbs/documents/status")
async def get_document_status(body: DocumentStatusRequest):
    svc = _get_service()
    docs = await svc.db.get_documents(body.kb_id)
    doc = next((d for d in docs if d["id"] == body.doc_id), None)
    # ...其余逻辑不变...
```

**分块预览：**
```python
@router.post("/kbs/documents/chunks")
async def get_document_chunks(body: DocumentChunksRequest):
    svc = _get_service()
    result = await asyncio.to_thread(
        svc.vector_store.get_chunks_paginated,
        body.doc_id,
        body.kb_id,
        page=body.page,
        page_size=body.page_size,
    )
    # ...其余逻辑不变...
```

---

### Task 9: 重构 sessions.py

**Files:**
- Modify: `src/api/routes/sessions.py`

- [ ] **Step 1: 新增 Pydantic 模型，重构 3 个路由**

```python
from pydantic import BaseModel


class SessionMessagesRequest(BaseModel):
    """会话消息请求体。"""
    session_id: str

class SessionDeleteRequest(BaseModel):
    """会话删除请求体。"""
    session_id: str
```

重构路由：

```python
@router.post("/sessions/list")
async def list_sessions():
    # 内容与当前 GET /sessions 一致
    ...


@router.post("/sessions/messages")
async def get_session_messages(body: SessionMessagesRequest):
    session_id = body.session_id
    # 其余逻辑不变
    ...


@router.post("/sessions/delete")
async def delete_session(body: SessionDeleteRequest):
    session_id = body.session_id
    # 其余逻辑不变
    ...
```

---

### Task 10: 前端 api.js + index.html + chat.html 改造

**Files:**
- Modify: `nginx/html/js/api.js`
- Modify: `nginx/html/index.html` (2 处直接 fetch)
- Modify: `nginx/html/chat.html` (1 处直接 fetch)

- [ ] **Step 1: 重写 api.js**

```javascript
// nginx/html/js/api.js — REST API helpers with trace ID support

const API_BASE = '/api';

function generateTraceId() {
    return crypto.randomUUID?.() ||
        'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
            const r = Math.random() * 16 | 0;
            return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });
}

async function apiRequest(path, options = {}) {
    const traceId = generateTraceId();
    const url = `${API_BASE}${path}`;
    const config = {
        headers: {
            'Content-Type': 'application/json',
            'X-Trace-ID': traceId,
        },
        ...options,
    };

    // Don't set Content-Type for FormData (browser sets with boundary)
    if (options.body instanceof FormData) {
        if (config.headers) delete config.headers['Content-Type'];
    }

    const response = await fetch(url, config);
    const body = await response.json().catch(() => null);

    if (!body) throw new Error('请求失败');
    if (body.code === 'AUTH_REQUIRED' || body.code === 'AUTH_TOKEN_EXPIRED') {
        throw new Error('AUTH_REQUIRED');
    }
    if (body.code !== 'SUCCESS') throw new Error(body.message || '请求失败');
    return body.data;
}

// ====== Knowledge Bases ======
async function listKBs() {
    return apiRequest('/kbs/list', { method: 'POST', body: JSON.stringify({}) });
}

async function createKB(name, description = '') {
    return apiRequest('/kbs', {
        method: 'POST',
        body: JSON.stringify({ name, description }),
    });
}

async function deleteKB(kbId) {
    return apiRequest('/kbs/delete', {
        method: 'POST',
        body: JSON.stringify({ kb_id: kbId }),
    });
}

// ====== Documents ======
async function listDocuments(kbId) {
    return apiRequest('/kbs/documents/list', {
        method: 'POST',
        body: JSON.stringify({ kb_id: kbId }),
    });
}

async function uploadDocument(kbId, file) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('kb_id', kbId);
    return apiRequest('/kbs/documents/upload', {
        method: 'POST',
        body: formData,
    });
}

// ====== Toast Notification ======
// ...（showToast, hideToast 不变）...

// ====== Session History ======
async function fetchSessions() {
    return apiRequest('/sessions/list', { method: 'POST', body: JSON.stringify({}) });
}

async function fetchSessionMessages(sessionId) {
    return apiRequest('/sessions/messages', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId }),
    });
}

async function deleteSessionAPI(sessionId) {
    return apiRequest('/sessions/delete', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId }),
    });
}
```

- [ ] **Step 2: 修改 index.html 中的直连 fetch**

`index.html:541` — 文档状态轮询（上传后轮询）：

```javascript
// 原来:
const statusResp = await fetch(`${API_BASE}/kbs/${selectedKbId}/documents/${docId}/status`);

// 改为:
const traceId = generateTraceId();
const statusResp = await fetch(`${API_BASE}/kbs/documents/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Trace-ID': traceId },
    body: JSON.stringify({ kb_id: selectedKbId, doc_id: docId }),
});
```

`index.html:628-630` — 分块预览：

```javascript
// 原来:
const resp = await fetch(`${API_BASE}/kbs/${_chunkKbId}/documents/${_chunkDocId}/chunks?page=${page}&page_size=${_chunkPageSize}`);

// 改为:
const traceId = generateTraceId();
const resp = await fetch(`${API_BASE}/kbs/documents/chunks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Trace-ID': traceId },
    body: JSON.stringify({ kb_id: _chunkKbId, doc_id: _chunkDocId, page, page_size: _chunkPageSize }),
});
```

`index.html:243` — 认证 verify：

```javascript
// 原来:
const r = await fetch('/api/auth/verify');

// 改为:
const traceId = generateTraceId();
const r = await fetch('/api/auth/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Trace-ID': traceId },
    body: JSON.stringify({}),
});
```

- [ ] **Step 3: 修改 chat.html 中的直连 fetch**

`chat.html:139` — 认证 verify（同 index.html）：

```javascript
// 改为:
const r = await fetch('/api/auth/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Trace-ID': generateTraceId() },
    body: JSON.stringify({}),
});
```

> 注意：直接在 HTML 中使用 `generateTraceId()` 函数，该函数定义在 `api.js` 中，需确保 `api.js` 先于 inline script 加载（当前 `chat.html:132` 行加载 `api.js`，inline script 在 `:134` 行）。

---

### Task 11: 修改 chat.js SSE 追加 trace_id

**Files:**
- Modify: `nginx/html/js/chat.js`

- [ ] **Step 1: 在 SSE URL 中追加 `&trace_id=xxx`**

找到 `chat.js:243-247`，将 params 构建改为：

```javascript
const traceId = crypto.randomUUID?.() ||
    'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0;
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });

const params = new URLSearchParams({
    session_id: currentSessionId,
    kb_id: kbId,
    query: query,
    trace_id: traceId,        // ← 新增
});
```

---

### Task 12: 修改 login.html JSON body

**Files:**
- Modify: `nginx/html/login.html`

- [ ] **Step 1: 修改 login fetch 调用**

`login.html:35`：

```javascript
// 原来:
const r = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ account: acct, password: pw })
});

// 改为:
const traceId = crypto.randomUUID();
const r = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Trace-ID': traceId },
    body: JSON.stringify({ account: acct, password: pw }),
});
```

---

### Task 13: 测试更新

**Files:**
- Modify: `tests/api/test_knowledge_base.py`
- Modify: `tests/api/test_documents.py`

- [ ] **Step 1: 更新 test_knowledge_base.py 的 3 个测试**

```python
@patch("src.api.routes.knowledge_base._get_service")
def test_list_kbs(mock_get_service):
    """POST /api/kbs/list 返回知识库列表。"""
    mock_svc = mock_get_service.return_value
    mock_svc.list_knowledge_bases = AsyncMock(return_value=[
        ("kb-1", "年报知识库"),
        ("kb-2", "财报知识库"),
    ])

    response = client.post("/api/kbs/list", json={})

    assert response.status_code == 200


@patch("src.api.routes.knowledge_base._get_service")
def test_delete_kb_exists(mock_get_service):
    """POST /api/kbs/delete 删除已存在的知识库。"""
    mock_svc = mock_get_service.return_value
    mock_svc.delete_knowledge_base = AsyncMock(return_value=(True, "知识库已删除"))

    response = client.post("/api/kbs/delete", json={"kb_id": "kb-1"})

    assert response.status_code == 200


@patch("src.api.routes.knowledge_base._get_service")
def test_delete_kb_not_found(mock_get_service):
    """POST /api/kbs/delete 不存在的知识库返回 404。"""
    mock_svc = mock_get_service.return_value
    mock_svc.delete_knowledge_base = AsyncMock(return_value=(False, "知识库不存在"))

    response = client.post("/api/kbs/delete", json={"kb_id": "kb-missing"})

    assert response.status_code == 404
```

- [ ] **Step 2: 更新 test_documents.py 的 2 个测试**

```python
@patch("src.api.routes.documents._get_service")
def test_get_documents(mock_get_service):
    """POST /api/kbs/documents/list 返回文档列表。"""
    mock_svc = mock_get_service.return_value
    mock_svc.get_documents = AsyncMock(return_value=[
        {"id": "doc-1", "filename": "report.pdf", "status": "ready"},
    ])

    response = client.post("/api/kbs/documents/list", json={"kb_id": "kb-1"})

    assert response.status_code == 200


@patch("src.api.routes.documents._get_service")
def test_upload_document(mock_get_service):
    """POST /api/kbs/documents/upload 返回 202 Accepted。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.add_document.return_value = "test-doc-uuid"

    response = client.post(
        "/api/kbs/documents/upload",
        data={"kb_id": "kb-1"},
        files={"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")},
    )

    assert response.status_code == 202
```

---

### Task 14: 更新接口契约与文档

**Files:**
- Modify: `docs/api-contract.md`
- Modify: `docs/api_contract.md`
- Modify: `src/api/README.md`
- Modify: `src/chat_manager.py`

- [ ] **Step 1: 更新 docs/api-contract.md**

```markdown
# Financial QA API — Contract

## Base URL

Production: `http://localhost/api/`
Development: `http://localhost:8000/`
OpenAPI Docs: `http://localhost/api/docs`

## 通用说明

- 除 `/api/health` 和 `/api/chat/stream` 外，所有端点使用 POST 方法
- 请求头 `X-Trace-ID` 可选传，后端自动处理
- 响应头含 `X-Trace-ID`，可用于请求链路追踪

## Knowledge Bases

### List all KBs

`POST /api/kbs/list`
Content-Type: `application/json`
Body: `{}`

Response 200:
```json
{"code": "SUCCESS", "message": "操作成功", "data": [
  {"id": "uuid", "name": "库名称"}
]}
```

### Create KB

`POST /api/kbs`
Content-Type: `application/json`
Body: `{"name": "库名称", "description": "可选描述"}`
Response 201: ...

### Delete KB

`POST /api/kbs/delete`
Content-Type: `application/json`
Body: `{"kb_id": "uuid"}`
Response 200: ...

## Documents

### List documents
`POST /api/kbs/documents/list`
Body: `{"kb_id": "uuid"}`
Response 200: ...

### Upload document (async)
`POST /api/kbs/documents/upload`
Content-Type: `multipart/form-data`
Fields: `file` (PDF/DOCX/TXT), `kb_id` (uuid)
Response 202: ...

### Document processing status
`POST /api/kbs/documents/status`
Body: `{"kb_id": "uuid", "doc_id": "uuid"}`
Response 200: ...

### Document chunk preview
`POST /api/kbs/documents/chunks`
Body: `{"kb_id": "uuid", "doc_id": "uuid", "page": 1, "page_size": 50}`
Response 200: ...

## Chat (SSE Streaming)

### Stream chat response
`GET /api/chat/stream?session_id={sid}&kb_id={kb_id}&query={question}&trace_id={traceId}`
Content-Type: `text/event-stream`
Events: status / token / citation / done / error

## Health

### Health check
`GET /api/health`
Response 200: `{"status": "ok"}`
```

- [ ] **Step 2: 更新 docs/api_contract.md** — 同步上述内容

- [ ] **Step 3: 更新 src/api/README.md**

```markdown
# API 路由层

提供 RAG 系统的 HTTP 接口，基于 FastAPI 实现，支持 SSE 流式推送。

## 文件说明

| 文件 | 职责 |
|---|---|
| `main.py` | FastAPI 应用入口、CORS、中间件注册、生命周期管理 |
| `routes/__init__.py` | 路由注册枢纽 |
| `routes/health.py` | 健康检查端点 `GET /api/health` |
| `routes/knowledge_base.py` | 知识库 CRUD：列表 `POST /api/kbs/list`、创建 `POST /api/kbs`、删除 `POST /api/kbs/delete` |
| `routes/documents.py` | 文档管理：列表 `POST /api/kbs/documents/list`、上传 `POST .../upload`、状态 `POST .../status`、分块 `POST .../chunks` |
| `routes/chat.py` | 流式 RAG 问答 `GET /api/chat/stream`，推送 status/token/citation/done 事件 |
| `routes/sessions.py` | 会话管理：列表 `POST /api/sessions/list`、消息 `POST .../messages`、删除 `POST .../delete` |
```

- [ ] **Step 4: 更新 src/chat_manager.py:116 docstring**

```python
# 原来:
"""在 DELETE /api/sessions/{id} 端点中被调用，"""
# 改为:
"""在 POST /api/sessions/delete 端点中被调用，"""
```

---

### Task 15: 前后端 JS 版本号 bump

**Files:**
- Modify: `nginx/html/index.html`
- Modify: `nginx/html/chat.html`

- [ ] **Step 1: 更新版本号**

```html
<!-- index.html:237 -->
<script src="js/api.js?v=11"></script>

<!-- chat.html:132-133 -->
<script src="js/api.js?v=11"></script>
<script src="js/chat.js?v=2"></script>
```

---

### Task 16: 验证

- [ ] **Step 1: 运行 pytest 确认全部通过**

```bash
pytest tests/ -v
```

Expected: 所有测试 PASS

- [ ] **Step 2: 运行 ruff 检查**

```bash
ruff check .
```

Expected: 无错误

- [ ] **Step 3: 清除遗留调试代码**

```bash
grep -rn "print(" src/ --include="*.py" | grep -v "__pycache__" | grep -v "print("
```
检查并移除非必要的 `print()`、TODO 注释和调试代码。
