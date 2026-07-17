# FastAPI 依赖注入重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 6 个 API 模块的模块级 `_get_service()` 单例模式统一为 FastAPI `Depends(get_app_service)` 依赖注入

**Architecture:** 新增 `src/api/dependencies.py` 集中管理依赖，6 个 API 路由文件改为从函数参数接收 AppService，测试改用 `dependency_overrides` 代替 `@patch`

**Tech Stack:** FastAPI Depends, pytest dependency_overrides, unittest.mock

## Global Constraints

- 不改 `src/services/app_service.py`
- 不改 `src/infra/redis_client.py`
- 不改 `src/middleware/auth.py`
- 不改 `src/api/health.py`
- 所有 `@patch("src.api.xxx._get_service")` 替换为 `mock_app_service` fixture
- 测试必须全部通过

---

### Task 1: 创建 `src/api/dependencies.py`

**Files:**
- Create: `src/api/dependencies.py`

**Interfaces:**
- Produces: `async def get_app_service() -> AppService` — FastAPI dependency callable

- [ ] **Step 1: Create dependencies.py**

```python
"""FastAPI 依赖注入 — 集中管理 API 层的共享依赖。"""

from src.services.app_service import AppService

_service: AppService | None = None


async def get_app_service() -> AppService:
    """FastAPI 依赖：提供 AppService 单例。

    延迟初始化：首次调用时创建实例，后续复用。
    避免模块导入阶段产生网络或数据库连接。
    """
    global _service
    if _service is None:
        _service = AppService()
    return _service
```

- [ ] **Step 2: 验证 import 正常**

Run: `cd /mnt/d/code/demo/AIAgent/corporate_rag && python -c "from src.api.dependencies import get_app_service; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/api/dependencies.py
git commit -m "feat: add centralized AppService DI dependency"
```

---

### Task 2: 修改测试基础设施 — conftest.py 添加 mock_app_service fixture

**Files:**
- Modify: `tests/api/conftest.py`

**Interfaces:**
- Produces: `mock_app_service` fixture — returns `AsyncMock`, registered as dependency_overrides

- [ ] **Step 1: 在 conftest.py 新增 fixture**

在 `tests/api/conftest.py` 的文件末尾（最后一个 fixture 之后）添加：

```python
@pytest.fixture
def mock_app_service():
    """替换 get_app_service 依赖，返回可配置的 AsyncMock。

    每个测试可通过此 fixture 配置 AppService 各方法的返回值。
    测试结束后自动清理 dependency_overrides。
    """
    from src.api.dependencies import get_app_service

    mock = AsyncMock()
    app.dependency_overrides[get_app_service] = lambda: mock
    yield mock
    app.dependency_overrides.clear()
```

同时在文件头部的 import 区域确认已有 `from unittest.mock import AsyncMock`。如果没有，添加：

```python
from unittest.mock import AsyncMock
```

- [ ] **Step 2: 验证 fixture 可以被测试使用**

Run: `cd /mnt/d/code/demo/AIAgent/corporate_rag && python -m pytest tests/api/test_health.py -v --tb=short 2>&1 | tail -5`
Expected: 测试仍通过（health.py 未改动，不受影响）

- [ ] **Step 3: Commit**

```bash
git add tests/api/conftest.py
git commit -m "test: add mock_app_service fixture for DI-based mocking"
```

---

### Task 3: 修改 auth.py + test_auth.py

**Files:**
- Modify: `src/api/auth.py`
- Modify: `tests/api/test_auth.py`

**Changes in auth.py:**
- 删除模块级 `_service: AppService | None = None`
- 删除 `def _get_service() -> AppService`
- 删除 `from src.services.app_service import AppService`
- 新增 `from fastapi import Depends`
- 新增 `from src.api.dependencies import get_app_service`
- login: `svc = _get_service()` → `svc: AppService = Depends(get_app_service)`
- verify_token: `svc = _get_service()` → `svc: AppService = Depends(get_app_service)`
- logout: `svc = _get_service()` → `svc: AppService = Depends(get_app_service)`

**Changes in test_auth.py:**
- 每个 `@patch("src.api.auth._get_service")` 替换为 `mock_app_service` 参数
- `mock_get_service.return_value` 替换为 `mock_app_service`

- [ ] **Step 1: 修改 auth.py**

```diff
-from fastapi import APIRouter, Cookie
+from fastapi import APIRouter, Cookie, Depends
 from fastapi.responses import JSONResponse
 from loguru import logger

 from src.api.model.request import LoginRequest
 from src.api.model.response import LoginResponse, VerifyResponse
-from src.services.app_service import AppService
+from src.services.app_service import AppService  # 仍被 _service 的类型标注使用... 不对，_service 要删
+from src.api.dependencies import get_app_service
 from src.config.response_codes import Code
 from src.infra.errors import AuthError
 from src.infra.auth.user_auth import UserAuth

 router = APIRouter()

-_service: AppService | None = None

-
-def _get_service() -> AppService:
-    ...

-
 @router.post("/auth/login")
-async def login(body: LoginRequest) -> LoginResponse:
-    svc = _get_service()
+async def login(body: LoginRequest, svc: AppService = Depends(get_app_service)) -> LoginResponse:
     ...
-    await UserAuth.store_token_async(get_redis_client(), token, user_id)
-    await svc.db.update_user_token(user_id, token)
+    await UserAuth.store_token_async(get_redis_client(), token, user_id)
+    await svc.db.update_user_token(user_id, token)
     return LoginResponse(token=token, user_id=user_id)


 @router.post("/auth/verify")
-async def verify_token(token: str = Cookie(None)) -> VerifyResponse:
+async def verify_token(
+    token: str = Cookie(None),
+    svc: AppService = Depends(get_app_service),
+) -> VerifyResponse:
     if not token:
         return VerifyResponse(valid=False)
-    svc = _get_service()
     uid = await UserAuth.get_user_id_from_token_async(get_redis_client(), token)
     return VerifyResponse(valid=uid is not None, user_id=uid)


 @router.post("/auth/logout")
-async def logout(token: str = Cookie(None)) -> JSONResponse:
+async def logout(
+    token: str = Cookie(None),
+    svc: AppService = Depends(get_app_service),
+) -> JSONResponse:
     if token:
-        svc = _get_service()
         await UserAuth.delete_token_async(get_redis_client(), token)
     return JSONResponse({"message": "已退出登录"})
```

注意：
- `src.services.app_service.AppService` 仍需保留 import，因为 `svc: AppService` 参数类型标注需要它
- `login` 函数里 `svc` 原来放在局部变量，现在在参数里，调用 `svc.db.get_user_by_account()` 时用参数里的 `svc`

- [ ] **Step 2: 修改 test_auth.py**

每个测试函数做以下替换：

```diff
-from unittest.mock import patch, AsyncMock
+from unittest.mock import AsyncMock

-@patch("src.api.auth._get_service")
-def test_login_new_user_auto_register(mock_get_service, mock_hash, client):
-    mock_svc = mock_get_service.return_value
+def test_login_new_user_auto_register(mock_app_service, mock_hash, client):
+    mock_svc = mock_app_service
     mock_svc.db.get_user_by_account = AsyncMock(return_value=None)
     ...
```

遍历所有 8 个测试函数，逐一替换：
- `test_login_new_user_auto_register`
- `test_login_existing_user_correct_password`
- `test_login_wrong_password`
- `test_login_missing_password` — 注意这个测试不涉及 `_get_service`，但需确认无误
- `test_verify_token_valid`
- `test_verify_no_token`
- `test_logout`
- `test_anonymous_new_user` / `test_anonymous_existing_user` — 不在 `_get_service` 范围内，不修改

具体每个函数的对照：

| 测试函数 | 原参数顺序 | 新参数顺序 | 改动 |
|---------|-----------|-----------|------|
| test_login_new_user_auto_register | `mock_get_service, mock_hash, client` | `mock_app_service, mock_hash, client` | `mock_svc = mock_app_service` |
| test_login_existing_user_correct_password | `mock_get_service, mock_hash, client` | `mock_app_service, mock_hash, client` | 同上 |
| test_login_wrong_password | `mock_get_service, mock_hash, client` | `mock_app_service, mock_hash, client` | 同上 |
| test_login_missing_password | `client` | 不改 | 无 `@patch`，跳过 |
| test_verify_token_valid | `mock_get_service, mock_get_uid, client` | `mock_app_service, mock_get_uid, client` | `mock_svc = mock_app_service` |
| test_verify_no_token | `mock_get_service, client` | `mock_app_service, client` | 直接删除 mock_get_service 引用（不使用 mock_svc） |
| test_logout | `mock_get_service, mock_delete, client` | `mock_app_service, mock_delete, client` | `mock_svc = mock_app_service` |
| test_anonymous_new_user | `client` | 不改 | 跳过 |
| test_anonymous_existing_user | `client` | 不改 | 跳过 |

- [ ] **Step 3: 运行 auth 测试验证**

Run: `cd /mnt/d/code/demo/AIAgent/corporate_rag && python -m pytest tests/api/test_auth.py -v --tb=short 2>&1 | tail -20`
Expected: 所有 auth 测试通过

- [ ] **Step 4: Commit**

```bash
git add src/api/auth.py tests/api/test_auth.py
git commit -m "refactor: replace auth.py _get_service() with FastAPI Depends"
```

---

### Task 4: 修改 knowledge_base.py + test_knowledge_base.py

**Files:**
- Modify: `src/api/knowledge_base.py`
- Modify: `tests/api/test_knowledge_base.py`

**Changes in knowledge_base.py:**
- 删除 `_service: AppService | None = None`
- 删除 `def _get_service() -> AppService`
- 新增 `from fastapi import Depends`
- 新增 `from src.api.dependencies import get_app_service`
- list_kbs: 参数加 `svc: AppService = Depends(get_app_service)`, 删内部 `svc = _get_service()`
- create_kb: 同上
- delete_kb: 同上

- [ ] **Step 1: 修改 knowledge_base.py**

```diff
-from fastapi import APIRouter, Request
+from fastapi import APIRouter, Request, Depends

 from src.api.model.request import CreateKBRequest, KBDeleteRequest
 from src.api.model.response import CreateKBResponse, KBItem, KBDeleteResponse
 from src.services.app_service import AppService
+from src.api.dependencies import get_app_service
 from src.config.response_codes import Code
 from src.infra.errors import BusinessError

 router = APIRouter()

-# 单例服务实例（延迟初始化）
-_service: AppService | None = None
-
-
-def _get_service() -> AppService:
-    ...

-
 @router.post("/kbs/list")
-async def list_kbs(request: Request) -> list[KBItem]:
-    svc = _get_service()
+async def list_kbs(request: Request, svc: AppService = Depends(get_app_service)) -> list[KBItem]:
     ...

 @router.post("/kbs")
-async def create_kb(body: CreateKBRequest, request: Request) -> CreateKBResponse:
-    svc = _get_service()
+async def create_kb(body: CreateKBRequest, request: Request, svc: AppService = Depends(get_app_service)) -> CreateKBResponse:
     ...

 @router.post("/kbs/delete")
-async def delete_kb(body: KBDeleteRequest, request: Request) -> KBDeleteResponse:
-    svc = _get_service()
+async def delete_kb(body: KBDeleteRequest, request: Request, svc: AppService = Depends(get_app_service)) -> KBDeleteResponse:
     ...
```

- [ ] **Step 2: 修改 test_knowledge_base.py**

```diff
-from unittest.mock import patch, AsyncMock
+from unittest.mock import AsyncMock
```

每个测试函数替换 `@patch` 和 `mock_get_service`：

- `test_list_kbs(mock_get_service, auth_client)` → `test_list_kbs(mock_app_service, auth_client)` + `mock_svc = mock_app_service`
- `test_create_kb` 同上
- `test_create_kb_missing_name` — 这个测试原本有 `@patch` 但测试体内 `mock_get_service` 没有被使用（它测的是 422 校验）。可直接删 `@patch` 和 `mock_get_service` 参数。
- `test_delete_kb_exists` 同 test_list_kbs
- `test_delete_kb_not_found` 同 test_list_kbs

- [ ] **Step 3: 运行 knowledge_base 测试验证**

Run: `cd /mnt/d/code/demo/AIAgent/corporate_rag && python -m pytest tests/api/test_knowledge_base.py -v --tb=short 2>&1 | tail -15`
Expected: 5 个测试全部通过

- [ ] **Step 4: Commit**

```bash
git add src/api/knowledge_base.py tests/api/test_knowledge_base.py
git commit -m "refactor: replace knowledge_base.py _get_service() with FastAPI Depends"
```

---

### Task 5: 修改 documents.py + test_documents.py

**Files:**
- Modify: `src/api/documents.py`
- Modify: `tests/api/test_documents.py`

**Changes in documents.py:**
同样的模式：删除 `_service`、`_get_service()`，函数参数加 `svc: AppService = Depends(get_app_service)`

影响的路由函数：
- `get_documents`
- `upload_document`
- `document_status`
- `document_chunks`
- `delete_document`

- [ ] **Step 1: 修改 documents.py**

```diff
-from fastapi import APIRouter, Request, UploadFile, File, Form
+from fastapi import APIRouter, Request, UploadFile, File, Form, Depends
 ...
 from src.services.app_service import AppService
+from src.api.dependencies import get_app_service
 ...
-router = APIRouter()
-
-_service: AppService | None = None
-
-
-def _get_service() -> AppService:
-    ...
```

每个路由函数的 `svc = _get_service()` 替换为参数注入。

- [ ] **Step 2: 修改 test_documents.py**

8 个测试函数逐一替换 `@patch` 和 `mock_get_service.return_value` → `mock_app_service`

- [ ] **Step 3: 运行 documents 测试验证**

Run: `cd /mnt/d/code/demo/AIAgent/corporate_rag && python -m pytest tests/api/test_documents.py -v --tb=short 2>&1 | tail -15`
Expected: 8 个测试全部通过

- [ ] **Step 4: Commit**

```bash
git add src/api/documents.py tests/api/test_documents.py
git commit -m "refactor: replace documents.py _get_service() with FastAPI Depends"
```

---

### Task 6: 修改 sessions.py + test_sessions.py

**Files:**
- Modify: `src/api/sessions.py`
- Modify: `tests/api/test_sessions.py`

**Changes in sessions.py:**
同样的模式。影响的路由函数：
- `list_sessions`
- `session_messages`
- `delete_session`

- [ ] **Step 1: 修改 sessions.py**

```diff
-from fastapi import APIRouter, Request
+from fastapi import APIRouter, Request, Depends
 ...
 from src.services.app_service import AppService
+from src.api.dependencies import get_app_service
```

每个路由函数的 `svc = _get_service()` 替换为参数注入。

- [ ] **Step 2: 修改 test_sessions.py**

6 个测试函数逐一替换。

- [ ] **Step 3: 运行 sessions 测试验证**

Run: `cd /mnt/d/code/demo/AIAgent/corporate_rag && python -m pytest tests/api/test_sessions.py -v --tb=short 2>&1 | tail -15`
Expected: 6 个测试全部通过

- [ ] **Step 4: Commit**

```bash
git add src/api/sessions.py tests/api/test_sessions.py
git commit -m "refactor: replace sessions.py _get_service() with FastAPI Depends"
```

---

### Task 7: 修改 kb_eval.py + test_kb_eval.py

**Files:**
- Modify: `src/api/kb_eval.py`
- Modify: `tests/api/test_kb_eval.py`

- [ ] **Step 1: 修改 kb_eval.py**

```diff
-from fastapi import APIRouter, Request
+from fastapi import APIRouter, Request, Depends
 ...
 from src.services.app_service import AppService
+from src.api.dependencies import get_app_service
```

`get_latest_kb_eval` 函数的 `svc = _get_service()` 替换为参数注入。

- [ ] **Step 2: 修改 test_kb_eval.py**

2 个测试函数替换。

- [ ] **Step 3: 运行 kb_eval 测试验证**

Run: `cd /mnt/d/code/demo/AIAgent/corporate_rag && python -m pytest tests/api/test_kb_eval.py -v --tb=short 2>&1 | tail -10`
Expected: 2 个测试通过

- [ ] **Step 4: Commit**

```bash
git add src/api/kb_eval.py tests/api/test_kb_eval.py
git commit -m "refactor: replace kb_eval.py _get_service() with FastAPI Depends"
```

---

### Task 8: 修改 chat.py + test_chat.py

**Files:**
- Modify: `src/api/chat.py`
- Modify: `tests/api/test_chat.py`

- [ ] **Step 1: 修改 chat.py**

```diff
-from fastapi import APIRouter, Request
+from fastapi import APIRouter, Request, Depends
 ...
 from src.services.app_service import AppService
+from src.api.dependencies import get_app_service
```

`chat_stream` 函数的 `svc = _get_service()` 替换为参数注入。

- [ ] **Step 2: 修改 test_chat.py**

1 个测试函数替换。

- [ ] **Step 3: 运行 chat 测试验证**

Run: `cd /mnt/d/code/demo/AIAgent/corporate_rag && python -m pytest tests/api/test_chat.py -v --tb=short 2>&1 | tail -8`
Expected: 1 个测试通过

- [ ] **Step 4: Commit**

```bash
git add src/api/chat.py tests/api/test_chat.py
git commit -m "refactor: replace chat.py _get_service() with FastAPI Depends"
```

---

### Task 9: 全量回归验证

- [ ] **Step 1: 运行全部 API 测试**

Run: `cd /mnt/d/code/demo/AIAgent/corporate_rag && python -m pytest tests/api/ -v --tb=short 2>&1 | tail -40`
Expected: 34 个测试全部通过（或与改动前数量一致）

- [ ] **Step 2: 验证 src 目录下不再有 _get_service 模块级变量**

Run: `cd /mnt/d/code/demo/AIAgent/corporate_rag && grep -rn "_service:.*AppService.*None" src/api/ --include="*.py"`
Expected: 无输出（不再有 `_service` 模块级变量）

- [ ] **Step 3: 验证 _get_service 只在 health.py 存在**

Run: `cd /mnt/d/code/demo/AIAgent/corporate_rag && grep -rn "def _get_service" src/api/ --include="*.py"`
Expected: 只有 `src/api/health.py` 有 `_get_service`
