## Why

当前 trace_id 基础设施完备（中间件注入、ContextVar 传播、Langfuse 关联），但日志格式未渲染 `{extra[trace_id]}`，导致 trace_id 无法通过日志文本排查问题。同时 MySQL、ChromaDB、MinIO 等关键操作缺乏结构化日志，无法通过 trace_id 串联请求链路。

## What Changes

- **loguru 日志格式**：`main.py` 加 `logger.remove()` + 文件 sink，输出格式含 `{extra[trace_id]}`
- **日志路径**：开发环境 `logs/`，Docker 环境 `/data/logs/`，通过 `LOG_DIR` 环境变量控制；目录不存在时自动创建
- **日志级别**：INFO，过滤 DEBUG 噪音
- **stdlib→loguru**：3 个文件从 `logging.getLogger` 改为 `from loguru import logger`
- **MySQL 关键操作加日志**：`mysql_db.py` 中 7 个方法在 SQL 执行后加 `logger.info("SQL ...")`，含操作名和关键参数
- **MinIO 操作加日志**：`documents.py` 中 upload/download 调用后加日志
- **ChromaDB 操作加日志**：`app_service.py` 和 `vector_store.py` 中关键操作后加日志
- **RAG 链加日志**：`rag_chain.py` 中 search/rerank/stream 三个阶段加关键指标日志
- **后台任务加 trace_id**：`_process_document_task` 开头获取 trace_id 并记录

## Capabilities

### New Capabilities
- `trace-logging`: 全链路 trace_id 日志集成

### Modified Capabilities
<!-- No existing specs are affected -->

## Impact

- `src/api/main.py` — loguru sink 配置
- `src/middleware/response_envelope.py` — stdlib→loguru
- `src/infra/llm/langfuse_tracing.py` — stdlib→loguru
- `src/infra/llm/prompt_manager.py` — stdlib→loguru
- `src/infra/db/mysql_db.py` — 7 个方法加 SQL 日志
- `src/infra/db/vector_store.py` — 3 个方法加 ChromaDB 日志
- `src/api/routes/documents.py` — MinIO 日志 + 后台任务 trace_id
- `src/app_service.py` — ChromaDB 删除日志
- `src/rag_chain.py` — 检索/精排/生成三阶段日志
- `.env.template` — 新增 `LOG_DIR` 环境变量说明
