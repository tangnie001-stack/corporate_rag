## ADDED Requirements

### Requirement: 统一日志配置

系统 SHALL 提供 `src/core/logging.py` 模块，提供统一的 Loguru 配置入口。
支持 `write_to_file` 参数区分 API（写文件）和 CLI（只输出 stderr）两种模式。
支持 `configure_trace_id` 参数，API 模式下自动注入 trace_id patcher。CLI 模式不注入（无 HTTP 上下文）。

#### Scenario: API 模式调用 setup_logging

- **WHEN** `setup_logging(write_to_file=True, configure_trace_id=True)` 被调用
- **THEN** Loguru 配置为：①移除默认 sink；②stdout 彩色控制台 sink（INFO 级别）；③`app_{date}.log` 文件 sink（INFO 级别，按天轮转，保留 7 天）；④`error.log` 文件 sink（ERROR 级别，100MB 轮转，保留 30 天，`enqueue=True`）

#### Scenario: CLI 模式调用 setup_logging

- **WHEN** `setup_logging(write_to_file=False)` 被调用
- **THEN** 只配置 stderr 彩色控制台 sink，不写文件

#### Scenario: InterceptHandler 收编三方库日志

- **WHEN** uvicorn、fastapi 等标准库 logging 模块的日志被发出
- **THEN** InterceptHandler 将其路由至 Loguru，使用统一格式输出，不重复

#### Scenario: CLI 工具使用统一日志

- **WHEN** `check_retrieval.py` 或 `eval_ragas.py` 启动时调用 `setup_logging(write_to_file=False)`
- **THEN** 日志格式与 API 一致（trace_id 字段保留但值为空，CLI 无 HTTP 请求上下文），不再使用 Loguru 默认 sink

#### Scenario: trace_id 注入

- **WHEN** `setup_logging(configure_trace_id=True)` 且任何日志消息被记录
- **THEN** 该消息中自动包含当前请求的 trace_id（通过 ContextVar 注入），格式为 `{time} | {level} | {trace_id} | {name}:{function}:{line} - {message}`

### Requirement: 降级型异常补日志

`chat_manager.py` 中 3 处无日志的 `except Exception` 块 SHALL 补充 `logger.warning` 说明降级原因。
注意：`mysql_db.py` 和 `vector_store.py` 中已知业务正常的静默 catch 保持不动。

#### Scenario: chat_manager.py 静默 except 补日志

- **WHEN** `chat_manager.py` 中的无日志 except 块捕获异常
- **THEN** 记录 `logger.warning("Redis 不可用，切换到 InMemory 模式")` 等具体描述
