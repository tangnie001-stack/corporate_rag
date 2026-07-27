## 1. 实体定义 — entities/

- [x] 1.1 创建 `src/infra/db/entities/` 目录和 `__init__.py`
- [x] 1.2 创建 `entities/search.py`：ChunkResult（id/content/metadata/distance/bm25_score）、ChunkQueryResult（items/total/page/page_size），所有字段带注释
- [x] 1.3 创建 `entities/kb.py`：KbEntity、KbListItem
- [x] 1.4 创建 `entities/document.py`：DocEntity（含所有 document 表字段）
- [x] 1.5 创建 `entities/chat.py`：SessionEntity、SessionListItem、MessageEntity
- [x] 1.6 创建 `entities/user.py`：UserEntity
- [x] 1.7 创建 `entities/eval_report.py`：EvalReportEntity
- [x] 1.8 验证：`python3 -c "from src.infra.db.entities import KbEntity, DocEntity, SessionEntity, MessageEntity, UserEntity, EvalReportEntity, ChunkResult, ChunkQueryResult; print('OK')"`

## 2. vector_store 拆包 + 返回类型化

- [x] 2.1 创建 `src/infra/db/vector_store/` 目录和 `__init__.py`（导出 VectorStore + ChunkResult）
- [x] 2.2 创建 `embedding.py`：迁移 DashScopeEmbeddingFunction
- [x] 2.3 创建 `client.py`：迁移连接管理 + collection 缓存（_get_client, _collection_name, get_or_create_collection）
- [x] 2.4 创建 `store.py`：迁移 add_chunks / delete_document / delete_collection / list_collections
- [x] 2.5 创建 `search.py`：迁移 similarity_search / similarity_search_all / get_chunks_by_doc_id / get_chunks_paginated，将 dict → ChunkResult
- [x] 2.6 删除原 `vector_store.py`
- [x] 2.7 更新所有 import 路径：agent_service.py、app_service.py、document_service.py、retrieval.py、workflow.py、api/documents.py、cli/*.py
- [x] 2.8 验证：`pytest tests/ -v -k "vector" 2>&1 | head -30`

## 3. BM25 索引返回类型化

- [x] 3.1 修改 `src/infra/search/bm25_index.py` 的 search() 返回 `list[ChunkResult]`
- [x] 3.2 修改 `rrf_fusion()` 参数和返回类型为 `list[ChunkResult]`
- [x] 3.3 修改 `build_index()` 入参注解为 `list[ChunkData]`（从 parsers/base.py 导入，保持和调用方 document_service.py 一致）
- [x] 3.4 验证：`pytest tests/ -v -k "bm25" 2>&1 | head -20`

## 4. 检索链路适配

- [x] 4.1 修改 `src/rag/retrieval.py` 的 search() 函数签名和内部调用适配 ChunkResult
- [x] 4.2 修改 `rerank_results()` 参数为 `list[ChunkResult]`，内部访问 `.content` 代替 `r["content"]`
- [x] 4.3 验证：`pytest tests/ -v -k "retrieval" 2>&1 | head -20`

## 5. AgentState 和 Graph 节点适配

- [x] 5.1 修改 `src/rag/context.py`：启用 `@dataclass(slots=True)`
- [x] 5.2 修改 `src/agents/graph/state.py`：`retrieval_results: List[ChunkResult]`，`contexts: List[RAGContext]`
- [x] 5.3 修改 `src/agents/graph/nodes.py` 中 retrieve_node / grader_node / rerank_node / format_node：
  - retrieval_results 从 dict.get() 改为属性访问
  - rerank_node 直接存 `list[RAGContext]`，不做 dict 转换
  - generate_node 直接读 contexts 为 RAGContext 列表，不做 `RAGContext(**c)` 重新构造
  - format_node 直接读 contexts 属性访问
- [x] 5.4 验证：`pytest tests/ -v -k "agent" 2>&1 | head -30`

## 6. CLI 文件适配

- [x] 6.1 修改 `src/cli/check_retrieval.py`：属性访问代替 dict.get()
- [x] 6.2 修改 `src/cli/eval_ragas_generate.py`：属性访问代替 dict.get()
- [x] 6.3 修改 `src/cli/eval_ragas.py`：更新 import 路径（vector_store → vector_store.search）
- [x] 6.4 验证：`python3 -c "from src.cli import *; print('OK')"`

## 7. MySQL Repo 拆分 — mysql_db/ 包

- [x] 7.1 创建 `src/infra/db/mysql_db/` 目录和 `__init__.py`（导出 MySQLDB + 所有 Repo）
- [x] 7.2 创建 `pool.py`：从 mysql_db.py 迁移连接管理（`__init__` / `_get_pool` / `close` / `init_db`）
- [x] 7.3 创建 `kb_repo.py` — KbRepo（get_or_create_kb / get_all_kb / get_kb_by_name / get_kb_name_by_id / delete_kb / soft_delete_kb），返回 KbListItem/KbEntity
- [x] 7.4 创建 `document_repo.py` — DocumentRepo（add_document / get_documents / get_doc_names / update_document_status / update_document_meta_info / soft_delete_document / soft_delete_documents_by_kb），返回 DocEntity
- [x] 7.5 创建 `chat_repo.py` — ChatRepo（create_session / get_sessions / get_session_by_id / get_messages / save_message / delete_session_and_messages），返回 SessionEntity/SessionListItem/MessageEntity
- [x] 7.6 创建 `user_repo.py` — UserRepo（add_user / get_user_by_account / update_user_token / get_user_by_token），返回 UserEntity
- [x] 7.7 创建 `eval_repo.py` — EvalRepo（ensure_eval_report_table / insert_eval_report / get_latest_eval_report），参数/返回均为 EvalReportEntity
- [x] 7.8 删除原 `mysql_db.py`，更新 `src/infra/db/__init__.py` 导出来源为 `mysql_db/` 包
- [x] 7.9 验证：`python3 -c "from src.infra.db.mysql_db import MySQLDB, KbRepo, DocumentRepo, ChatRepo, UserRepo, EvalRepo; print('OK')"` + `pytest tests/infra/db/test_mysql_db.py -v 2>&1 | head -20`

## 8. Service 层改用 Repo

- [x] 8.1 修改 `src/services/kb_service.py`：使用 KbRepo
- [x] 8.2 修改 `src/services/document_service.py`：使用 DocumentRepo
- [x] 8.3 修改 `src/services/auth_service.py`：使用 UserRepo
- [x] 8.4 修改 `src/services/app_service.py`：使用 ChatRepo + EvalRepo + 添加 get_chunks_paginated 方法
- [x] 8.5 修改 `src/chat/persistence.py`：注入 ChatRepo，抛弃 MySQLDB
- [x] 8.6 修改 `src/chat/manager.py`：`set_mysql_db()` 改为 `set_chat_repo()`
- [x] 8.7 验证：`pytest tests/services/ -v 2>&1 | tail -30`

## 9. API 层适配

- [x] 9.1 修改 `src/api/documents.py`：get_document_chunks 通过 AppService 调 get_chunks_paginated，不走 `svc.vector_store`
- [x] 9.2 修改 `src/api/documents.py`：`c["id"]` → `c.id`、`c.get("metadata", {}).get("page", 1)` → `c.metadata.get("page", 1)`、`result["items"]` → `result.items`、`result["total"]` → `result.total`
- [x] 9.3 验证：`pytest tests/ -v 2>&1 | tail -20`

## 10. 测试适配 + 收尾

- [x] 10.1 更新 `tests/conftest.py`：mysql_db fixture 适配，mock 返回值改为 Entity dataclass
- [x] 10.2 更新 `tests/infra/db/test_mysql_db.py`：assert 改为实体属性比较
- [x] 10.3 更新 `tests/services/test_app_service.py`：@patch 路径更新
- [x] 10.4 更新 `tests/reset_data.py`：使用 Repo 替代 MySQLDB
- [x] 10.5 全量跑测试：`pytest tests/ -v 2>&1 | tail -50`
- [x] 10.6 lint 检查：`ruff check . --fix && ruff format .`
- [x] 10.7 确认无遗留 `print()`、TODO、调试代码
- [x] 10.8 最终验证：`pytest tests/ -v && ruff check .`
