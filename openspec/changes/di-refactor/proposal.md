## Why

当前 6 个 API 模块各自维护一份 `_service` + `_get_service()` 模板代码，用模块级变量实现 AppService 单例。存在三个问题：代码重复（6 份相同模板）、测试 mock 脆弱（`@patch` 路径字符串容易写错）、依赖不透明（路由函数签名上看不出它依赖 AppService）。

## What Changes

- 新增 `src/api/dependencies.py`，集中管理 `get_app_service` 依赖
- 6 个 API 模块删除各自 `_get_service()` 代码，改用 `Depends(get_app_service)`
- 测试文件从 `@patch("src.api.xxx._get_service")` 改为 `mock_app_service` fixture
- `conftest.py` 新增 `mock_app_service` fixture（使用 FastAPI `dependency_overrides`）

## Capabilities

### New Capabilities
- `api-di`: FastAPI 依赖注入机制，统一管理 API 层的 AppService 依赖

### Modified Capabilities
<!-- No existing capability spec changes - this is purely an implementation refactor -->

## Impact

- 新增：`src/api/dependencies.py`
- 修改：`src/api/auth.py`、`knowledge_base.py`、`documents.py`、`sessions.py`、`chat.py`、`kb_eval.py`
- 修改：`tests/api/conftest.py` 及 6 个测试文件
- 不改：`health.py`（轻量级保持原样）、`middleware/auth.py`、`services/app_service.py`
