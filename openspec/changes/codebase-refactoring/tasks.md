## 1. 前置准备

- [ ] 1.1 追加 CLAUDE.md 规约：代码目录结构、层间调用规则、文件大小红线、LLM 自检清单
- [ ] 1.2 更新 CLAUDE.md 数据流章节和规则章节（删除 old/ 引用）

## 2. 死代码清理

- [ ] 2.1 删除 `old/` 整个目录
- [ ] 2.2 删除 `src/api/routes/` 空目录
- [ ] 2.3 删除 `src/document_loader.py`（无人引用）

## 3. 基础设施 — redis_client 独立

- [ ] 3.1 新建 `src/infra/redis_client.py`，提供 `get_redis_client()` 工厂函数
- [ ] 3.2 更新 `middleware/auth.py`：直接导入 `redis_client`，不再走 AppService
- [ ] 3.3 更新 `AppService.__init__`：删除 `self._redis = ...` 和 `redis_client` property

## 4. API 层 — SSE 格式化独立

- [ ] 4.1 新建 `src/api/sse_utils.py`，从 `api/chat.py` 移入 6 个 sse_* 函数
- [ ] 4.2 更新 `api/chat.py`：删除 SSE 函数，改为 `from src.api.sse_utils import ...`

## 5. 业务服务层 — services/ 包

- [ ] 5.1 新建 `src/services/__init__.py`，导出 AppService
- [ ] 5.2 从 `src/app_service.py` 提取知识库 CRUD 到 `src/services/kb_service.py`（KBService 类）
- [ ] 5.3 从 `src/app_service.py` 提取文档处理逻辑到 `src/services/document_service.py`（DocumentService 类），包含 `_process_document_task` 和 `_enrich_chunk_pages`
- [ ] 5.4 从 `src/app_service.py` 提取对话问答到 `src/services/chat_service.py`（ChatService 类）
- [ ] 5.5 重写 `src/services/app_service.py`：AppService 持有 KBService/DocumentService/ChatService 三个子 service，编排跨子 service 操作，纯转发方法委托子 service

## 6. RAG 流水线拆分 — rag/ 包

- [ ] 6.1 新建 `src/rag/__init__.py`，导出 RAGChain 和 RAGContext
- [ ] 6.2 新建 `src/rag/retrieval.py`：提取检索逻辑（search/rerank/rerank_results）和查询改写（classify/expand/condense/decompose/rewrite）为纯函数
- [ ] 6.3 新建 `src/rag/prompt.py`：提取 format_context/build_prompt/build_simple_prompt 为纯函数
- [ ] 6.4 新建 `src/rag/stream.py`：提取 estimate_usage 和 stream_answer（含指数退避重试）为纯函数
- [ ] 6.5 新建 `src/rag/chain.py`：RAGChain 主类+ RAGContext，chat_with_citations() 拆为 _handle_simple_route/_handle_short_query/_handle_search_error/_handle_normal_flow 四个子方法

## 7. 对话管理拆分 — chat/ 包

- [ ] 7.1 新建 `src/chat/__init__.py`，导出 ChatManager
- [ ] 7.2 新建 `src/chat/persistence.py`：PersistenceService 类，从 ChatManager 提取 save_session_async/save_messages_async/cleanup_session
- [ ] 7.3 重写 `src/chat/manager.py`：ChatManager 仅保留 Redis/InMemory 会话 CRUD，MySQL 持久化委托给 PersistenceService

## 8. 调用方 import 路径更新

- [ ] 8.1 更新 `src/api/*.py`（7 个文件）：import 改为 `from src.services.app_service import AppService`
- [ ] 8.2 更新 `src/api/chat.py`：import `RAGChain` 改为 `from src.rag.chain import RAGChain`
- [ ] 8.3 更新 `src/app_service.py`：import 改为 `from src.rag import RAGChain, RAGContext` 和 `from src.chat import ChatManager`
- [ ] 8.4 更新 `src/cli/eval_ragas.py`：4 处 import 路径改为新路径
- [ ] 8.5 确认以上所有调用方均已更新，再无引用旧路径的文件

## 9. 删除旧文件（已无引用）

- [ ] 9.1 删除 `src/app_service.py`
- [ ] 9.2 删除 `src/rag_chain.py`
- [ ] 9.3 删除 `src/chat_manager.py`

## 10. 测试迁移

- [ ] 10.1 新建测试子目录：`tests/services/`、`tests/rag/`、`tests/chat/`、`tests/parsers/`、`tests/infra/db/`、`tests/infra/search/`、`tests/infra/llm/`、`tests/infra/chunking/`、`tests/infra/auth/`、`tests/config/`、`tests/middleware/`、`tests/eval/`
- [ ] 10.2 将 `test_rag_chain.py`（28K 行）拆为 4 个文件移入 `tests/rag/`：
  - `test_chain.py`：对应 chain.py 的编排逻辑和 chat_with_citations
  - `test_retrieval.py`：对应 retrieval.py 的检索和查询改写
  - `test_prompt.py`：对应 prompt.py 的 prompt 构建
  - `test_stream.py`：对应 stream.py 的流式生成和 token 估算
- [ ] 10.3 将 `test_rag_chain_tracing.py` 移入 `tests/rag/`
- [ ] 10.4 将 `test_chat_manager.py` 移入 `tests/chat/`
- [ ] 10.5 将 `test_app_service.py` 移入 `tests/services/`
- [ ] 10.6 将 parser 相关测试（test_base / test_docx_parser / test_pymupdf_parser / test_txt_parser / test_router）移入 `tests/parsers/`
- [ ] 10.7 将 infra 测试移入对应子目录（db/search/llm/chunking/auth）
- [ ] 10.8 将 `test_middleware.py` 和 `test_api_error.py` 移入 `tests/middleware/`
- [ ] 10.9 将 `test_settings.py` �� `test_response_codes.py` 移入 `tests/config/`
- [ ] 10.10 将 `test_eval_ragas.py` 移入 `tests/eval/`，将 `tests/unit/test_chunk_scorer.py` 移入 `tests/eval/`
- [ ] 10.11 更新 `tests/conftest.py` 和 `tests/reset_data.py` 的 import 路径
- [ ] 10.12 删除 `tests/unit/` 空目录

## 11. 验证

- [ ] 11.1 运行 `pytest tests/ -v` 确认全部通过
- [ ] 11.2 运行 `ruff check .` 确认无错误
- [ ] 11.3 检查无遗留 `print()`、TODO 或调试代码
- [ ] 11.4 确认 `old/` 相关引用在 CLAUDE.md 中已清除
