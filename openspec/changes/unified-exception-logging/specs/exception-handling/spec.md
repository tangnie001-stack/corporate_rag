## ADDED Requirements

### Requirement: AppError 异常层次

系统 SHALL 提供 `src/infra/errors.py` 模块，定义按类型分类的异常层次体系：

- `AppError` — 基类，携带 `code`、`message`、`status`
- `BusinessError(AppError)` — 业务规则冲突，默认 status=400
- `AuthError(AppError)` — 认证授权失败，默认 status=401
- `ValidationError(AppError)` — 参数校验失败，默认 status=422
- `SystemError(AppError)` — 基础设施故障，默认 status=503

#### Scenario: BusinessError 被抛出

- **WHEN** 业务规则不满足（如"文档不存在"），`raise BusinessError(Code.DOC_NOT_FOUND, "文档不存在", status=404)`
- **THEN** `@app.exception_handler(AppError)` 捕获，记录 `logger.warning`，返回 `{"code": "DOC_NOT_FOUND", "message": "文档不存在", "data": null}`，status=404

#### Scenario: SystemError 被抛出

- **WHEN** 基础设施故障（如"Redis 连接失败"），`raise SystemError(Code.INTERNAL_ERROR, "Redis 连接失败", status=503)`
- **THEN** `@app.exception_handler(AppError)` 捕获，记录 `logger.exception`（含完整 traceback），返回 `{"code": "INTERNAL_ERROR", "message": "服务暂时不可用", "data": null}`，status=503

#### Scenario: ApiError 替换

- **WHEN** 旧代码中所有 `raise ApiError(...)` 被调用
- **THEN** 替换为 `raise BusinessError(...)` 或对应的异常类型，旧 `api_error.py` 文件被删除

### Requirement: 全局异常处理增强

Router 层所有未捕获异常 SHALL 由 `@app.exception_handler` 集中处理，每个 handler SHALL 调用 `logger.exception()` 记录完整 traceback。Handler 返回的 JSONResponse SHALL 使用统一 `{"code", "message", "data"}` 格式。

#### Scenario: HTTPException 处理

- **WHEN** 路由抛出 `HTTPException(404)` 或 `HTTPException(405)`
- **THEN** `@app.exception_handler(HTTPException)` 捕获，记录 `logger.exception`，返回统一格式 JSONResponse，status_code 保持原值

#### Scenario: RequestValidationError 处理

- **WHEN** 请求参数校验失败（FastAPI 默认 422）
- **THEN** `@app.exception_handler(RequestValidationError)` 捕获，记录 `logger.exception`，返回 `{"code": "VALIDATION_ERROR", "message": "参数校验失败", "data": null}`，status=422

#### Scenario: 兜底 UnknownError

- **WHEN** 路由抛出未预期的异常（如 `ValueError`、`RuntimeError`）
- **THEN** `@app.exception_handler(Exception)` 作为兜底捕获，记录 `logger.exception`，返回 `{"code": "INTERNAL_ERROR", "message": "服务器内部错误", "data": null}`，status=500

### Requirement: 响应包装集中

`ResponseEnvelopeMiddleware` SHALL 只做两件事：①成功响应包装（读 body → `{"code":"SUCCESS", "data": ...}`）；②Auth 中间件的 `except AppError` 捕获。对 `status_code >= 400` 的响应 SHALL 直接透传，不二次解析 body。

#### Scenario: 成功响应包装

- **WHEN** 路由返回 JSON body `{"user_id": 123, "name": "Alice"}`
- **THEN** dispatch 返回 `{"code": "SUCCESS", "message": "操作成功", "data": {"user_id": 123, "name": "Alice"}}`

#### Scenario: 异常响应透传

- **WHEN** `@app.exception_handler` 返回 `{"code": "NOT_FOUND", "message": "...", "data": null}`，status=404
- **THEN** dispatch 直接透传，不做二次包装

#### Scenario: Auth 异常通过 dispatch 处理

- **WHEN** Auth 中间件 `raise AuthError(Code.AUTH_REQUIRED, "请先登录", status=401)`
- **THEN** dispatch 的 `except AppError` 捕获，返回 `{"code": "AUTH_REQUIRED", "message": "请先登录", "data": null}`，status=401

### Requirement: Auth 中间件规范化

Auth 中间件的错误路径 SHALL 使用 `raise AuthError` 而非 `return JSONResponse`。
涉及位置：`auth.py:38`（token 为空）、`auth.py:47`（token 无效）。

#### Scenario: Auth token 为空

- **WHEN** 请求无 token cookie，且路径为 `/api/kbs/*`
- **THEN** `raise AuthError(Code.AUTH_REQUIRED, Code.AUTH_REQUIRED_MSG, status=401)`，不直接 return JSONResponse

#### Scenario: Auth token 无效

- **WHEN** token 已过期或被篡改
- **THEN** `raise AuthError(Code.AUTH_TOKEN_EXPIRED, Code.AUTH_TOKEN_EXPIRED_MSG, status=401)`，不直接 return JSONResponse
