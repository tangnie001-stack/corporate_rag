# Trace ID Logging 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 所有日志输出 trace_id，MySQL/ChromaDB/MinIO/RAG 关键操作加结构化日志

**Architecture:** loguru format 配置 → stdlib→loguru 转换 → 各模块逐方法加 logger.info

**Tech Stack:** Python 3.11+ / FastAPI / loguru / aiomysql / ChromaDB / MinIO

## Global Constraints

- 日志格式固定：`"{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {extra[trace_id]:32} | {message}"`
- 日志路径从 `LOG_DIR` 环境变量读取（默认 `logs`），启动时 `os.makedirs` 创建
- 日志级别 INFO，按天轮转，保留 7 天
- 所有 `logger.info` 使用 loguru 的 `{}` 格式化风格

---

### Task 1: Loguru 格式配置 + 环境变量

**文件:**
- 修改: `src/api/main.py`

**内容:**
1. 在 `main.py` 的 loguru 配置区域，调用 `logger.remove()` 移除默认 sink
2. 添加文件 sink，路径 `{LOG_DIR}/app_{time:YYYY-MM-DD}.log`，format 包含 `{extra[trace_id]}`
3. `LOG_DIR` 从环境变量读取（默认 `logs`）
4. 启动时 `os.makedirs(LOG_DIR, exist_ok=True)`
5. `.env.template` 末尾添加 `# LOG_DIR=/data/logs` 注释

```python
# 日志目录（开发环境 logs/，Docker 设 /data/logs/）
LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {extra[trace_id]:32} | {message}"
logger.remove()
logger.add(
    f"{LOG_DIR}/app_{{time:YYYY-MM-DD}}.log",
    format=_LOG_FORMAT,
    rotation="1 day",
    retention="7 days",
    level="INFO",
)
```

- [ ] 实现 main.py 配置修改
- [ ] 实现 .env.template 添加 LOG_DIR 注释
- [ ] 运行 `ruff check src/api/main.py` 通过
- [ ] 提交

### Task 2: stdlib → loguru 转换（3 个文件）

**文件:**
- 修改: `src/middleware/response_envelope.py`
- 修改: `src/infra/llm/langfuse_tracing.py`
- 修改: `src/infra/llm/prompt_manager.py`

**每个文件的改动：**
- 删 `import logging`
- 删 `logger = logging.getLogger(__name__)`
- 加 `from loguru import logger`
- prompt_manager.py 还需将 `from src.config.prompts` import 移到文件顶部（避免 E402）

- [ ] response_envelope.py 改 loguru
- [ ] langfuse_tracing.py 改 loguru
- [ ] prompt_manager.py 改 loguru + 修复 import 顺序
- [ ] 运行 `ruff check` 这三个文件
- [ ] 提交

### Task 3: MySQL 操作日志（7 个方法）

**文件:**
- 修改: `src/infra/db/mysql_db.py`

**每个方法在 return 前加一行 logger.info：**

| 方法 | 日志内容 |
|------|---------|
| `get_document()` | `"SQL get_document: doc_id={} found={}", doc_id, row is not None` |
| `soft_delete_document()` | `"SQL soft_delete_document: doc_id={} rows_affected={}", doc_id, rowcount` |
| `soft_delete_documents_by_kb()` | `"SQL soft_delete_documents_by_kb: kb_id={} rows_affected={}", kb_id, rowcount` |
| `soft_delete_kb()` | `"SQL soft_delete_kb: kb_id={} found={}", kb_id, ok` |
| `get_documents()` | `"SQL get_documents: kb_id={} count={}", kb_id, len(rows)` |
| `update_document_status()` | `"SQL update_document_status: doc_id={} status={} chunk_count={}", doc_id, status, chunk_count` |
| `add_document()` | `"SQL add_document: doc_id={} kb_id={} filename={} status={}", doc_id, kb_id, filename, status` |

注意：有 `return` 在 `async with` 块内的方法，需要先赋值再 return。

- [ ] 实现 7 个 MySQL 方法日志
- [ ] 运行 `ruff check src/infra/db/mysql_db.py`
- [ ] 提交

### Task 4: MinIO + ChromaDB 操作日志

**文件:**
- 修改: `src/api/routes/documents.py` — MinIO upload/download 后加日志
- 修改: `src/app_service.py` — ChromaDB delete_collection 后加日志
- 修改: `src/infra/db/vector_store.py` — add_chunks / delete_document / similarity_search 日志

```python
# documents.py — fs.upload 调用后
logger.info("MinIO upload: key={} size={}", minio_key, len(contents))

# documents.py — FileStore().download 调用后
logger.info("MinIO download: key={} size={}", minio_key, len(contents) if contents else 0)

# app_service.py — delete_collection 调用后
logger.info("ChromaDB delete_collection: kb_id={}", kb_id)

# vector_store.py add_chunks — 替换现有 logger
logger.info("ChromaDB add_chunks: kb_id={} doc_id={} count={}", kb_id, doc_id, len(chunks))

# vector_store.py similarity_search — return 前加
logger.info("ChromaDB search: kb_id={} query_len={} results={}", kb_id, len(query), len(formatted))

# vector_store.py delete_document — 替换现有 logger
logger.info("ChromaDB delete_document: kb_id={} doc_id={} deleted={}", kb_id, doc_id, count)
```

- [ ] documents.py MinIO 日志
- [ ] app_service.py ChromaDB 日志
- [ ] vector_store.py 3 个方法日志
- [ ] 运行 `ruff check` 这三个文件
- [ ] 提交

### Task 5: RAG 链日志 + 后台任务

**文件:**
- 修改: `src/rag_chain.py` — search/rerank/stream 三阶段加日志
- 修改: `src/api/routes/documents.py` — `_process_document_task` 开头加日志

```python
# rag_chain.py search — 三个返回路径各加
logger.info("RAG search: kb_id={} query_len={} results={}", kb_id, len(query), len(results))

# rag_chain.py rerank
logger.info("RAG rerank: before={} after={}", len(results), len(contexts))

# rag_chain.py _stream_answer — 第一条 token 时
logger.info("RAG first_token_latency={:.0f}ms", latency)

# documents.py _process_document_task — 函数开头
logger.info("process_task start: doc_id={} filename={}", doc_id, filename)
```

- [ ] rag_chain.py search 日志（3 个返回路径）
- [ ] rag_chain.py rerank 日志
- [ ] rag_chain.py _stream_answer 首 token 延迟日志
- [ ] documents.py 后台任务启动日志
- [ ] 运行 `ruff check` 这两个文件
- [ ] 提交
