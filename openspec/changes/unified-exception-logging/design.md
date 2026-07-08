## Context

当前项目异常处理和日志存在以下问题：

- **日志配置分散**：`main.py` 中配置 Loguru，CLI 工具（`check_retrieval.py`、`eval_ragas.py`）无独立配置，使用默认 sink，格式不统一
- **traceback 覆盖率极低**：47 个 `except` 块中仅 2 处使用 `logger.exception`（含完整堆栈），无法基于日志快速定位线上问题
- **异常类型模糊**：~85% 的 `except` 块使用裸 `Exception`，无法区分业务错误与基础设施故障
- **异常处理路径分散**：`@app.exception_handler` 和 `ResponseEnvelopeMiddleware.dispatch()` 两处都在处理异常，职责重叠
- **Auth 中间件绕过包装层**：直接 `return JSONResponse`，不符合"业务层只 raise"的架构约定
- **重试策略重复实现**：`models.py`（装饰器）、`rag_chain.py`（内联）、`chat.py`（内联）三套实现，维护成本高

## Goals / Non-Goals

**Goals:**
- 所有异常路径都记录完整 traceback（`logger.exception`）
- 所有异常响应统一使用 `{"code", "message", "data"}` 格式
- 异常类型按性质分类（BusinessError / AuthError / SystemError），不再裸 `except Exception`
- 重试策略统一为 `with_retry` 装饰器，降级逻辑由调用方处理
- 三种异常模式清晰划分：降级型（吞掉+日志）、透传型（re-raise+日志）、拦截型（直接 raise）
- 响应包装集中到 `ResponseEnvelopeMiddleware`，业务层只 raise

**Non-Goals:**
- 不引入新的外部依赖（prometheus_client 待 ARMS 上线后评估）
- 不改变现有 API 响应格式（上层消费者无感知）
- 不改动 tests/ 现有测试（改后运行 pytest 确认不破坏）
- 不处理 CLI 工具（`cli/`）的异常体系（它们不走 FastAPI 栈）

## Decisions

### D1：异常层次 — 按类型分，不按模块分

- AppError 基类 → BusinessError/AuthError/ValidationError/SystemError
- 模块信息由 Code 枚举前缀（`DOC_*` / `FILE_*` / `KB_*`）承载
- 理由：异常 handler 按"怎么处理"分流（Business→友好提示，System→告警），不是按模块。模块 handler 逻辑相同，分类无意义

### D0：日志配置 — setup_logging 收拢配置

- `src/core/logging.py` 提供 `setup_logging()`，两个参数：
  - `write_to_file: bool = True` — API 模式写文件 + 控制台；CLI 模式仅控制台
  - `configure_trace_id: bool = False` — API 模式下自动注入 trace_id patcher；CLI 模式不注入
- `InterceptHandler` 收编 uvicorn/fastapi 等标准库日志，路由至 Loguru 统一管道
- 理由：API 和 CLI 共用 sink 配置代码，trace_id patcher 与 HTTP 请求上下文绑定

### D2：响应包装 — 双路分工

- `@app.exception_handler`：处理 Router 层所有异常（AppError / HTTPException / ValidationError / 兜底 Exception），每个 handler 中调 `logger.exception`
- `ResponseEnvelopeMiddleware.dispatch()`：只包装成功响应 + 接 Auth 层异常（`except AppError`，Auth 在 ExceptionMiddleware 外面）+ 自身兜底 `except Exception`
- `@app.exception_handler` 的响应已统一格式，dispatch 见到 `status_code >= 400` 直接透传
- 理由：利用 Starlette ExceptionMiddleware 的内置能力，不自建 catch-all

### D3：降级型 except 不动

- 降级型（非关键路径+有 fallback）保持捕获+日志+吞掉
- 只改两处：`chat_manager.py` 中 3 个无日志的 except 补 `logger.warning`；`models.py` 重试耗尽补 `logger.exception`
- 理由：降级型是设计意图，不是疏忽

### D4：重试策略 — 装饰器不处理降级

- `with_retry` 增强 `retryable_exceptions` 参数，不再只 `except Exception`
- 重试耗尽后 re-raise，降级逻辑由调用方 `try/except` 处理
- 理由：装饰器保持单一职责（重试），降级是业务决策

### D5：Auth 中间件

- 异常路径从 `return JSONResponse` 改为 `raise AuthError`
- dispatch 的 `except AppError` 会捕获并返回统一格式
- 理由：保持"业务层只 raise"的架构一致性

## Risks / Trade-offs

- [风险] `@app.exception_handler` 和 dispatch 两处都处理异常，可能混淆 → 分工明确：AppError 在 router 层通过 handler 处理，只有 Auth 的 AppError 通过 dispatch 处理
- [风险] Auth 改为 raise 后，异常传播路径变化，可能影响现有逻辑 → Auth 异常不涉及事务/资源清理，纯响应路径切换，风险低
- [风险] 重试策略统一可能改变现有退避行为（chat.py 当前用线性退避 → 改为指数退避）→ 这是期望的优化，不是降级
- [风险] `rag_chain.py` 的 rerank 降级逻辑（重试失败后用原始顺序）需额外 try/except → 设计上已经考虑（装饰器只重试，调用方自己处理降级）
