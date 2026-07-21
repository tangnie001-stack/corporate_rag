## Why

项目缺少集中式异常管理和日志规范化。问题表现为：
- ~85% 的 `except` 块使用裸 `Exception`，异常类型不精确
- 仅 2/47 处 `except` 块记录了完整 traceback（`logger.exception`），线上问题排查困难
- 日志配置分散在 `main.py` 中，CLI 工具使用默认配置，格式不统一
- 三套重试策略重复实现（装饰器 + 两处内联），维护成本高
- Auth 中间件直接返回 `JSONResponse` 绕过统一响应包装

## What Changes

1. **提取统一日志模块** — 将 Loguru 配置抽到 `src/core/logging.py`，API 和 CLI 统一调用
2. **异常层次体系** — 新增 `AppError / BusinessError / AuthError / SystemError`，按类型分类
3. **全局异常处理增强** — 增强 `@app.exception_handler` 并新增 AppError/Exception 兜底 handler，`ResponseEnvelopeMiddleware` 配合接 Auth 层异常，双路补充 traceback 日志
4. **Auth 中间件规范化** — 异常路径改为 `raise AuthError` 而非 `return JSONResponse`
5. **40+ except 块评审** — 保留降级型，为透传型补充 `logger.exception`
6. **重试策略统一** — `with_retry` 装饰器增强，收编 `rag_chain.py` / `chat.py` 中的内联重试
7. **Prometheus 指标** — 放入需求池，待 ARMS 上线后评估（**BREAKING**: 留 TODO 注释位）

## Capabilities

### New Capabilities
- `logging-config`: 集中式日志配置，Loguru 统一管道，API/CLI 双模式
- `exception-handling`: AppError 异常层次体系，全局异常 handler 集中管理
- `retry-unification`: 统一的重试装饰器，支持精确异常类型匹配

### Modified Capabilities

（无 — 现有 specs（demo-verification / evaluation-pipeline / retrieval-quality）不涉及异常处理和日志）

## Impact

- `src/core/logging.py` — 新增文件
- `src/infra/errors.py` — 新增文件，替代 `src/infra/api_error.py`（删除）
- `src/middleware/response_envelope.py` — 简化，只做成功响应包装 + Auth 异常兜底
- `src/api/main.py` — 增强现有 `@app.exception_handler`，新增 AppError 和 Exception 兜底 handler
- `src/middleware/auth.py` — 3 处 `return JSONResponse` → `raise AuthError`
- `src/models.py` — `with_retry` 增强
- `src/rag_chain.py`、`src/api/routes/chat.py` — 内联重试 → 统一装饰器
- `src/chat_manager.py` — 3 处无日志 except 补 `logger.warning`
- `BACKLOG.md` — 新增，记录待定项
- `CLAUDE-RULES.md` — 新增，记录架构规约
