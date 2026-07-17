# FastAPI 依赖注入重构：替换模块级 `_get_service()` 单例模式

## 背景

当前 6 个 API 模块各自维护一份 `_service` + `_get_service()` 模板代码，用模块级变量实现 AppService 单例。存在三个问题：

1. **代码重复**：相同的 `_get_service()` 模式在 6 个文件中各写一遍
2. **测试 mock 脆弱**：每个测试都需要 `@patch("src.api.xxx._get_service")`，路径字符串容易写错，重构时全量修改
3. **依赖不透明**：路由函数签名上看不出它依赖 AppService

## 方案

将 6 处 `_get_service()` 统一为一个 FastAPI `Depends` 依赖，集中管理在 `src/api/dependencies.py`。

## 改动范围

### 改动的文件

| 操作 | 文件 |
|------|------|
| 新增 | `src/api/dependencies.py` |
| 修改 | `src/api/auth.py` |
| 修改 | `src/api/knowledge_base.py` |
| 修改 | `src/api/documents.py` |
| 修改 | `src/api/sessions.py` |
| 修改 | `src/api/chat.py` |
| 修改 | `src/api/kb_eval.py` |
| 修改 | `tests/api/conftest.py` |
| 修改 | `tests/api/test_auth.py` |
| 修改 | `tests/api/test_knowledge_base.py` |
| 修改 | `tests/api/test_documents.py` |
| 修改 | `tests/api/test_sessions.py` |
| 修改 | `tests/api/test_chat.py` |
| 修改 | `tests/api/test_kb_eval.py` |

### 不改的文件

- `src/api/health.py` — `_ConfigService` 轻量级，只有一处使用，保持原样
- `src/infra/redis_client.py` — 仍作为直调函数保留
- `src/middleware/auth.py` — middleware 不支持 `Depends`
- `src/services/app_service.py` — 本身不动
- `src/rag/*` — 不在 API 层
- `tests/api/test_health.py` — 对应 health.py 不改

## 设计方案

### 1. 新建 `src/api/dependencies.py`

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

`_service` 从原 6 个文件集中到这一个文件，逻辑完全一致。

### 2. 每个 API 文件的标准改法

以 `knowledge_base.py` 为例：

```diff
 from fastapi import APIRouter, Request
+from fastapi import Depends
 from src.api.model.request import CreateKBRequest, KBDeleteRequest
 from src.api.model.response import CreateKBResponse, KBItem, KBDeleteResponse
 from src.services.app_service import AppService
+from src.api.dependencies import get_app_service
 from src.config.response_codes import Code
 from src.infra.errors import BusinessError

 router = APIRouter()

-# 删除：_service: AppService | None = None
-# 删除：def _get_service() -> AppService: ...

 @router.post("/kbs/list")
-async def list_kbs(request: Request) -> list[KBItem]:
-    svc = _get_service()
+async def list_kbs(request: Request, svc: AppService = Depends(get_app_service)) -> list[KBItem]:
     ...
```

6 个文件统一此模式：

| 文件 | 删除的模块级代码 | 新增的导入 | 改动的函数 |
|------|-----------------|-----------|-----------|
| `auth.py` | `_service`, `_get_service()` | `get_app_service`, `Depends` | login, verify_token, logout |
| `knowledge_base.py` | `_service`, `_get_service()` | 同上 | list_kbs, create_kb, delete_kb |
| `documents.py` | `_service`, `_get_service()` | 同上 | get_documents, upload_document, document_status, document_chunks, delete_document |
| `sessions.py` | `_service`, `_get_service()` | 同上 | list_sessions, session_messages, delete_session |
| `chat.py` | `_service`, `_get_service()` | 同上 | chat_stream |
| `kb_eval.py` | `_service`, `_get_service()` | 同上 | get_latest_kb_eval |

### 3. 测试改造

#### conftest.py — 新增 `mock_app_service` fixture

```python
# tests/api/conftest.py（新增）
from unittest.mock import AsyncMock
from src.api.dependencies import get_app_service

@pytest.fixture
def mock_app_service():
    """替换 get_app_service 依赖，返回可配置的 AsyncMock。"""
    mock = AsyncMock()
    app.dependency_overrides[get_app_service] = lambda: mock
    yield mock
    app.dependency_overrides.clear()
```

#### 每个测试文件的改法

以 `test_knowledge_base.py` 为例：

```diff
-from unittest.mock import patch, AsyncMock
+from unittest.mock import AsyncMock

-@patch("src.api.knowledge_base._get_service")
-def test_list_kbs(mock_get_service, auth_client):
-    mock_svc = mock_get_service.return_value
+def test_list_kbs(mock_app_service, auth_client):
+    mock_svc = mock_app_service
     mock_svc.list_knowledge_bases = AsyncMock(return_value=[...])
     response = auth_client.post("/api/kbs/list")
     ...
```

每个测试函数的变化：
- 删除 `@patch("src.api.xxx._get_service")`
- 参数名 `mock_get_service` → `mock_app_service`
- 删除 `mock_get_service.return_value`（因为 fixture 直接返回 mock 实例）

## 测试策略

1. 所有 `@patch` 替换为 `mock_app_service` fixture
2. 每个测试的 mock 行为配置（AsyncMock 的 return_value）保持不变
3. 运行全量 API 测试验证
4. 验证 `dependency_overrides.clear()` 不影响其他测试用例

## 不受影响的测试

- `test_health.py` — 不改，继续使用 `@patch("src.api.health._get_service")`
- `test_middleware.py` — 不改
- `test_auth_infra.py` — 不改
- `test_api_background_task.py` — 不改
- 所有非 API 测试 — 不改
