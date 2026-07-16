# Task 10: 测试目录重组 — 完成报告

## 状态：完成

## 操作摘要

### 1. 创建测试子目录
创建了 12 个子目录：
- `tests/services/`, `tests/rag/`, `tests/chat/`, `tests/parsers/`
- `tests/infra/db/`, `tests/infra/search/`, `tests/infra/llm/`  
- `tests/infra/chunking/`, `tests/infra/auth/`
- `tests/config/`, `tests/middleware/`, `tests/eval/`

### 2. 拆分 test_rag_chain.py
将 761 行的 `tests/test_rag_chain.py` 拆为 4 个文件：

| 目标文件 | 测试函数数 | 内容 |
|---------|-----------|------|
| `tests/rag/test_chain.py` | 17 | RAGContext, RAGChainInit, chat_with_citations 编排 |
| `tests/rag/test_retrieval.py` | 15 | search/rerank/classify/rewrite 方法 |
| `tests/rag/test_prompt.py` | 5 | build_prompt/format_context 方法 |
| `tests/rag/test_stream.py` | 1 | stream_answer 方法 |

### 3. 复制的测试文件（26 个）
- `tests/rag/`: test_rag_chain_tracing.py
- `tests/chat/`: test_chat_manager.py
- `tests/services/`: test_app_service.py
- `tests/parsers/`: test_base.py, test_docx_parser.py, test_pymupdf_parser.py, test_txt_parser.py, test_router.py
- `tests/infra/db/`: test_mysql_db.py, test_vector_store.py, test_file_store.py
- `tests/infra/search/`: test_bm25_index.py, test_query_router.py
- `tests/infra/llm/`: test_langfuse.py, test_prompt_manager_fallback.py
- `tests/infra/chunking/`: test_chunking.py, test_chunk_enhancer.py, test_chunk_validator.py
- `tests/infra/auth/`: test_auth.py
- `tests/config/`: test_settings.py, test_response_codes.py
- `tests/middleware/`: test_middleware.py, test_api_error.py
- `tests/eval/`: test_eval_ragas.py, test_chunk_scorer.py

### 4. 删除的原始文件和目录（28 个文件）
删除了 `tests/` 下所有旧测试文件和 `tests/unit/` 目录。

### 5. 修复的问题
- **Import 路径修复**: `tests/services/test_app_service.py` 中 `ApiError` → `AppError`
- **废弃装饰器清理**: 拆分后残留的装饰器已在 `test_prompt.py`, `test_stream.py`, `test_retrieval.py` 中清理
- **缺失的 @patch 装饰器**: 为 `test_retrieval.py` 中 `test_classify_clear` 和 `test_chain.py` 中 `test_saves_user_message_to_history` 补回了装饰器
- **缺失的 @pytest.mark.asyncio**: 为 `test_retrieval.py` 中 `test_search_returns_results` 补回

### 6. 验证结果
```bash
# 所有新位置测试收集正常：211 items collected
# 152 passed, 59 failed (全部失败为迁移前已存在的问题)
```

剩余的 59 个失败用例在迁移前的原始文件中同样失败，属于预存的问题（源代码重构后测试未同步更新），**非本次迁移引入**。

## 文件清单
迁移后测试目录结构：
```
tests/
├── chat/
├── config/
├── eval/
├── infra/  {db/, search/, llm/, chunking/, auth/}
├── middleware/
├── parsers/
├── rag/  {test_chain.py, test_retrieval.py, test_prompt.py, test_stream.py, test_rag_chain_tracing.py}
├── services/
├── api/
├── conftest.py
├── reset_data.py
├── test_models.py
└── test_dependencies.py
```
