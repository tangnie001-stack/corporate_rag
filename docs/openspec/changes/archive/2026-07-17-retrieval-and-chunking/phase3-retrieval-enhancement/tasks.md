## 1. 配置与基础设施

- [x] 1.1 将 TOP_K_RETRIEVAL 默认值从 10 改为 50
- [x] 1.2 添加 HYBRID_SEARCH_ENABLED 和 BM25_INDEX_DIR 配置项
- [x] 1.3 升级 Langfuse v3：docker-compose 添加 Clickhouse、MinIO、langfuse-worker 服务

## 2. 分块质量与增强

- [x] 2.1 实现 chunk_validator.py（ChunkData / ValidationWarning / ChunkQualityReport + validate_chunks）
- [x] 2.2 实现 ParentChildChunker（CHILD_SIZE=256, PARENT_SIZE=1024, RecursiveCharacterTextSplitter）
- [x] 2.3 PDF 表格提取：PyMuPDFParser._extract_tables_from_page + _table_to_markdown
- [x] 2.4 DOCX 表格提取：DocxParser._extract_tables + _docx_table_to_markdown

## 3. 检索增强

- [x] 3.1 实现 BM25Index（build_index / search / pickle 持久化）
- [x] 3.2 实现 rrf_fusion（RRF k=60, top_n=50）
- [x] 3.3 实现 QueryRouter（L0 正则规则：simple / vague / medium + L3 stub）
- [x] 3.4 实现查询改写 4 策略：expand / condense / decompose / passthrough

## 4. RAG Chain 改造

- [x] 4.1 拆分 RAGChain：search() / rerank() / stream_answer() 三个方法
- [x] 4.2 chat_with_citations() 集成意图路由和查询改写
- [x] 4.3 search() 中集成 Hybrid Search（Dense + BM25 并行 + RRF）

## 5. API 与 SSE

- [x] 5.1 SSE 状态事件：sse_status() / 三阶段推送
- [x] 5.2 异步上传端点：202 返回 + _process_document 后台任务
- [x] 5.3 状态轮询端点 GET .../status
- [x] 5.4 分块预览端点 GET .../chunks

## 6. 引用增强

- [x] 6.1 get_query_biased_snippet()：jieba 分词 + 关键词定位 + 上下文窗口
- [x] 6.2 _build_highlighted_snippet()：<mark> 标签高亮 HTML
- [x] 6.3 前端 chat.js 监听 status 事件 + 增强 citation 渲染

## 7. 验证

- [x] 7.1 为所有新增模块编写单元测试
- [x] 7.2 更新 RAGAS 评估支持 Parent-Child 分块参数对比
- [x] 7.3 全量测试通过 + ruff 检查
