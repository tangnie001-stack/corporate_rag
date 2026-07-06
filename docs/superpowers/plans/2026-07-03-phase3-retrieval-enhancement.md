# Phase 3 — 检索质量与核心增强 实施计划

## 目标
对 MVP 基础 RAG 链路进行全面增强，覆盖检索质量、分块策略、查询理解和用户体验。

## 依赖
- Python 3.11+, FastAPI, ChromaDB, LangChain
- DashScope API（LLM / Embedding / Reranker）
- MySQL 8.0 / Redis 7 / Docker Compose

## Batch 1 — 基础优化（4 items）

### Task 1.1 TOP_K_RETRIEVAL 默认 50
- 修改 `src/config/settings.py`: TOP_K_RETRIEVAL = 50
- 验证 `src/config/__init__.py` 的 wildcard 导出

### Task 1.2 分块质量校验
- 新建 `src/infra/chunk_validator.py`
- 数据类型: ChunkData, ValidationWarning, ChunkQualityReport (dataclass)
- count_tokens(): heuristic token counting (~2 chars/token)
- detect_garbled(): Unicode replacement char ratio
- validate_chunks(): quality check + report generation
- 测试: `tests/test_chunk_validator.py`

### Task 1.3 表格提取
- PDF: `pymupdf_parser.py` 添加 _extract_tables_from_page(), _table_to_markdown()
- DOCX: `docx_parser.py` 添加 _extract_tables(), _docx_table_to_markdown()
- 测试: `tests/test_pymupdf_parser.py`, `tests/test_docx_parser.py`

### Task 1.4 SSE 状态事件
- `chat.py` 添加 sse_status() helper
- RAGChain 拆分为 search() / rerank() / stream_answer()
- SSE 流中按阶段推送 status 事件
- 前端 `chat.js` 监听 status 事件并更新 UI

## Batch 2 — 检索核心（4 items）

### Task 2.1 Langfuse v3
- docker-compose 添加 clickhouse (24.12-alpine), minio (chainguard), langfuse-worker
- .env.template 添加 CLICKHOUSE_PASSWORD, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD
- Redis REDIS_AUTH → REDIS_PASSWORD 映射

### Task 2.2 RAGChain 拆分
- `rag_chain.py`: 将 chat_with_citations() 拆为 search() / rerank() / stream_answer()
- search(): async ChromaDB search (with hybrid option)
- rerank(): Reranker精排 + RAGContext 包装
- stream_answer(): prompt building + LLM stream

### Task 2.3 查询改写
- `rag_chain.py`: _classify_query(), _expand_query(), _condense_query(), _decompose_query()
- 4 策略: fuzzy_short→expand, colloquial→condense, compound→decompose, clear→passthrough

### Task 2.4 意图路由
- 新建 `src/infra/query_router.py`
- L0: SIMPLE_PATTERNS / VAGUE_PATTERNS / MEDIUM_PATTERNS (regex)
- L3: _llm_classify() stub → medium fallback
- 集成到 RAGChain.chat_with_citations()

## Batch 3 — 进阶检索（4 items）

### Task 3.1 Parent-Child 分块
- 新建 `src/infra/chunk_enhancer.py`: ParentChildChunker(CHILD_SIZE=256, PARENT_SIZE=1024)
- 子块 metadata 带 parent_content / parent_chunk_id
- 集成到 documents.py 处理流程

### Task 3.2 混合检索
- 新建 `src/infra/bm25_index.py`: BM25Index + rrf_fusion()
- 按 kb_id 隔离存储 pickle
- search() 中: asyncio.gather(Dense, BM25) → RRF(k=60, top_n=50)

### Task 3.3 异步上传
- upload_document 返回 202 + doc_id
- _process_document() 后台处理 (parse→chunk→index)
- GET .../status: 状态轮询 (progress map)
- GET .../chunks: 分块预览

### Task 3.4 引用增强
- get_query_biased_snippet(): jieba 分词 + keyword match + context window
- _build_highlighted_snippet(): <mark> HTML generation
- INLINE_CITATION_INSTRUCTION 追加到 system prompt

## 验证

1. `pytest tests/ -v` 全部通过
2. `ruff check .` 无错误
3. 上传测试文档到 KB，验证 SSE status 事件 (retrieving→reranking→generating)
4. 验证 citation 高亮渲染
5. 验证 async upload 的 status polling + chunk preview
