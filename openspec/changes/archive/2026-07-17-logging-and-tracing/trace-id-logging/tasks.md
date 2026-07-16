## 1. Loguru 格式与配置

- [ ] 1.1 在 `src/api/main.py` 中：`logger.remove()` + 文件 sink 配置，format 含 `{extra[trace_id]}`，日志目录从 `LOG_DIR` 环境变量读取（默认 `logs`），启动时自动创建目录
- [ ] 1.2 在 `.env.template` 中添加 `LOG_DIR` 环境变量说明

## 2. stdlib → loguru 转换

- [ ] 2.1 `src/middleware/response_envelope.py`: 删 `import logging` 和 `logger = logging.getLogger(__name__)`，加 `from loguru import logger`
- [ ] 2.2 `src/infra/llm/langfuse_tracing.py`: 同上
- [ ] 2.3 `src/infra/llm/prompt_manager.py`: 同上，并将 `from src.config.prompts` 移到文件顶部避免 E402

## 3. MySQL 操作日志

- [ ] 3.1 `mysql_db.py` `get_document()`: 末尾加 `logger.info("SQL get_document: doc_id={} found={}", ...)`
- [ ] 3.2 `mysql_db.py` `soft_delete_document()`: 末尾加 `logger.info("SQL soft_delete_document: doc_id={} rows_affected={}", ...)`
- [ ] 3.3 `mysql_db.py` `soft_delete_documents_by_kb()`: 末尾加 `logger.info("SQL soft_delete_documents_by_kb: kb_id={} rows_affected={}", ...)`
- [ ] 3.4 `mysql_db.py` `soft_delete_kb()`: 末尾改 `logger.info("SQL soft_delete_kb: kb_id={} found={}", ...)`
- [ ] 3.5 `mysql_db.py` `get_documents()`: 末尾加 `logger.info("SQL get_documents: kb_id={} count={}", ...)`
- [ ] 3.6 `mysql_db.py` `update_document_status()`: commit 后加 `logger.info("SQL update_document_status: doc_id={} status={} chunk_count={}", ...)`
- [ ] 3.7 `mysql_db.py` `add_document()`: commit 后加 `logger.info("SQL add_document: doc_id={} kb_id={} filename={} status={}", ...)`

## 4. MinIO 操作日志

- [ ] 4.1 `documents.py` fs.upload 调用后加 `logger.info("MinIO upload: key={} size={}", ...)`
- [ ] 4.2 `documents.py` FileStore().download 调用后加 `logger.info("MinIO download: key={} size={}", ...)`

## 5. ChromaDB 操作日志

- [ ] 5.1 `app_service.py` delete_collection 调用后加 `logger.info("ChromaDB delete_collection: kb_id={}", ...)`
- [ ] 5.2 `vector_store.py` `add_chunks()`: 日志改为 `"ChromaDB add_chunks: kb_id={} doc_id={} count={}"`
- [ ] 5.3 `vector_store.py` `delete_document()`: 日志改为 `"ChromaDB delete_document: kb_id={} doc_id={} deleted={}"`
- [ ] 5.4 `vector_store.py` `similarity_search()`: return 前加 `"ChromaDB search: kb_id={} query_len={} results={}"`

## 6. RAG 链日志

- [ ] 6.1 `rag_chain.py` `search()`: 三个返回路径各加 `"RAG search: kb_id={} query_len={} results={}"`
- [ ] 6.2 `rag_chain.py` `rerank()`: return 前加 `"RAG rerank: before={} after={}"`
- [ ] 6.3 `rag_chain.py` `_stream_answer()`: 首 token 时加 `"RAG first_token_latency={}ms"`

## 7. 后台任务 trace_id

- [ ] 7.1 `_process_document_task()` 开头加 `logger.info("process_task start: doc_id={} filename={}", ...)`

## 8. 测试验证

- [ ] 8.1 运行 `pytest tests/` 确认日志变更不破坏现有测试
- [ ] 8.2 手动检查 `logs/` 目录下日志文件，确认 trace_id 出现在每行日志中
