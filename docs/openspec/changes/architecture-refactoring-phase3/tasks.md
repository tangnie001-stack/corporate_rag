## 1. 基础设施 — 文件搬迁与 Graph 修复

- [ ] 1.1 创建 `src/utils/` 目录及 `__init__.py`；将 `infra/sse_utils.py` 搬迁至 `utils/sse.py` 并重命名文件（改名 sse_utils.py → sse.py）
- [ ] 1.2 将 `infra/errors.py` 搬迁至 `utils/errors.py`，更新所有 `from src.infra.errors import` 为 `from src.utils.errors import`（含 `tests/middleware/test_api_error.py`、`tests/services/test_app_service.py`）
- [ ] 1.3 将 `infra/desensitize.py` 搬迁至 `utils/desensitize.py`，更新所有 import
- [ ] 1.4 删除 `infra/chunking/enhancer.py`（死代码，旧版 ParentChildChunker）
- [ ] 1.5 `src/agents/graph/nodes.py`：`retrieve_node` 改为 async，`_search()` 辅助函数改为直接 `await search()`，消除 `asyncio.new_event_loop()`
- [ ] 1.6 `src/agents/graph/workflow.py`：确认 async `retrieve_node` 兼容 `add_conditional_edges`

## 2. 依赖重组 — AppService 与 ChatManager

- [ ] 2.1 `src/chat/manager.py`：删除 sync 方法 `get_history`、`add_message`、`clear_history`、`get_window`、`cleanup_session`、`_ensure_redis`、`_get_sync_redis`
- [ ] 2.2 `src/services/agent_service.py`：`add_message()` → `await add_message_async()`
- [ ] 2.3 `src/services/agent_service.py`：`llm`/`reranker` 改为构造函数可选参数（含 `models.py` 兜底），默认从 `models.get_llm()` / `get_rerank()` 获取
- [ ] 2.4 `src/api/sessions.py`：`asyncio.to_thread(cleanup_session)` → `await clear_history_async()`
- [ ] 2.5 `src/services/app_service.py`：移除 `rag_chain` 属性，直接持有 `chat_manager` 和 `bm25`
- [ ] 2.6 `src/api/chat.py`：`svc.rag_chain.chat_manager.*` → `svc.chat_manager.*`

## 3. 业务逻辑下沉 — DocumentService

- [ ] 3.1 `src/services/document_service.py`：新增 `async process_document()`，从 `api/documents.py` 迁入完整流水线逻辑
- [ ] 3.2 `src/services/document_service.py`：复制 `_enrich_chunk_pages()`、`_merge_tiny_chunks()` 为私有方法
- [ ] 3.3 `src/services/document_service.py`：删除 sync 版 `upload_and_process()`
- [ ] 3.4 `src/api/documents.py`：`_process_document_task()` 改为一行委托 `await svc.document.process_document(...)`
- [ ] 3.5 `src/api/documents.py`：删除 `_enrich_chunk_pages()`、`_merge_tiny_chunks()`、`_process_document_task()` 的业务逻辑

## 4. 收尾 — 删除 Path A 与测试清理

- [ ] 4.1 `src/rag/chain.py`：删除 `chat_with_citations()` 及依赖的 6 个子方法（`_handle_simple_route`、`_handle_short_query`、`_handle_search_error`、`_handle_no_results`、`_rewrite_if_needed`），保留 eval 接口（`search`、`rerank`、`stream_answer`）
- [ ] 4.2 `src/services/chat_service.py`：删除整个文件
- [ ] 4.3 `src/services/app_service.py`：删除 `ChatService` 创建和 `chat()` 方法
- [ ] 4.4 `src/cli/eval_ragas.py`：将 `from src.rag.chain import RAGChain` / `rag_chain.chat_with_citations()` 改为 `graph.ainvoke()` 执行评估
- [ ] 4.5 `tests/rag/test_chain.py`：删除 `test_chat_with_citations_*` 相关用例
- [ ] 4.6 `tests/services/test_chat_service.py`：删除整个测试文件
- [ ] 4.7 `tests/services/test_app_service.py`：删除 `test_chat_*` 和 `test_upload_and_process_*` 相关用例；新增 `test_process_document_*` 测试 async 版
- [ ] 4.8 `tests/chat/test_chat_manager.py`：删除 sync 方法测试用例

## 5. 验证

- [ ] 5.1 运行 `pytest tests/ -v` 确认全部通过
- [ ] 5.2 运行 `ruff check .` 确认无错误
- [ ] 5.3 检查无遗留 `print()`、TODO 或调试代码
- [ ] 5.4 启动服务，实测一个 SSE 请求确认端到端正常
