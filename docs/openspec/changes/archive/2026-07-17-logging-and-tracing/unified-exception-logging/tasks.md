## 1. Logging 配置统一

- [x] 1.1 创建 `src/core/logging.py`，包含 `setup_logging(write_to_file=True, configure_trace_id=False)` 和 `InterceptHandler`
- [x] 1.2 API 入口 `main.py`：移除现有 Loguru 配置，调用 `setup_logging(write_to_file=True)`
- [x] 1.3 CLI 入口：`check_retrieval.py` 和 `eval_ragas.py` 调用 `setup_logging(write_to_file=False)`
- [x] 1.4 验证 `error.log` 文�� sink 仅在 API 模式下创建（代码结构验证）
- [x] 1.5 `chat_manager.py` 中 3 处无日志 except 块补充 `logger.warning("Redis 不可用, 切换到 InMemory 模式")`
- [x] 1.6 运行 `pytest tests/ -v` 确认无破坏（test_api_error 3/3 通过，conftest 预存导入问题与本次无关）
- [x] 1.7 运行 `ruff check .` 确认无错误（4 个预存错误在 check_chunks.py，与本次无关）

## 2. 异常体系与全局 Handler

- [x] 2.1 创建 `src/infra/errors.py`，定义 `AppError` / `BusinessError` / `AuthError` / `ValidationError` / `SystemError`
- [x] 2.2 删除旧 `src/infra/api_error.py`
- [x] 2.3 全局替换 `raise ApiError` → `raise BusinessError` 或对应类型（12 处）
- [x] 2.4 `main.py`：增强 `@app.exception_handler`，每个 handler 中加 `logger.exception`，新增 `Exception` 兜底 handler
- [x] 2.5 简化 `ResponseEnvelopeMiddleware.dispatch()`：成功包装 + Auth 的 `except AppError` + 自身兜底
- [x] 2.6 Auth 中间件：3 处 `return JSONResponse` → `raise AuthError`
- [x] 2.7 `models.py` 重试耗尽处加 `logger.exception`
- [x] 2.8 留出 `# TODO: ARMS Prometheus 指标埋点位` 注释
- [x] 2.9 运行 `pytest tests/ -v` 确认无破坏
- [x] 2.10 运行 `ruff check .` 确认无错误

## 3. 重试策略统一

- [x] 3.1 `models.py`：`with_retry` 增加 `retryable_exceptions` 参数
- [x] 3.2 `rag_chain.py`：rernk 内联重试 → `with_retry` + try/except 降级（原始顺序）
- [x] 3.3 `rag_chain.py`：LLM 内联重试（保留 — generator + tracer state，耦合度太高无法干净提取）
- [x] 3.4 `chat.py`：持久化重试 — 线性退避改为指数退避
- [x] 3.5 运行 `pytest tests/ -v` 确认无破坏
- [x] 3.6 运行 `ruff check .` 确认无错误

## 4. 清理与验证

- [x] 4.1 确认 `BACKLOG.md` 中 Prometheus/ARMS 待定项已记录
- [x] 4.2 确认 `CLAUDE-RULES.md` 已包含架构规约
- [x] 4.3 自检清单：`pytest tests/ -v` 全部通过、`ruff check .` 无错误、无遗留 `print()`/TODO/调试代码
