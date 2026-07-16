## Why

MVP 阶段的基础 RAG 链路已跑通，但在实际金融文档问答中发现：检索候选不足导致 Reranker 效果有限、长文档上下文丢失、用户查询表达不规范时检索准确率低。Phase 3 聚焦检索质量和核心增强，从分块、检索、推理到交互全链路优化。

## What Changes

1. **检索参数调整** — TOP_K_RETRIEVAL 默认值从 10 提升至 50，为 Reranker 提供更多候选
2. **分块质量校验** — 入库前自动检测 token 过少和 Unicode 乱码分块，记录告警
3. **表格提取** — PDF (PyMuPDF find_tables) 和 DOCX (python-docx) 表格转为 Markdown 格式
4. **SSE 状态事件** — 流式响应中推送 retrieving / reranking / generating 三阶段状态
5. **Langfuse v3 升级** — 替换 v2，新增 Clickhouse (存储)、MinIO (S3)、Worker (异步处理)
6. **RAGChain 拆分** — 将 chat_with_citations() 拆为 search() / rerank() / stream_answer() 三个方法
7. **查询改写** — 4 种策略：模糊短查询扩展、口语化精简、对比类拆分、清晰直通
8. **意图路由** — L0 正则规则 + L3 LLM 兜底，分类为 simple/vague/medium/complex
9. **Parent-Child 分块** — child 256 tokens + parent 1024 tokens，子块 metadata 携带父段落全文
10. **混合检索** — BM25 词法检索与 Dense 语义检索并行执行，通过 RRF (k=60) 融合
11. **异步上传** — 上传接口返回 202，后台异步处理文档，支持状态轮询和分块预览
12. **引用增强** — jieba 分词实现 Query-Biased Snippet，回答中内联编号标注引用来源

## Capabilities

### New Capabilities
- `chunk-validation`: 分块质量自动校验，入库前检测异常分块并生成质量报告
- `table-extraction`: PDF 和 DOCX 文档中的表格自动提取并转为 Markdown
- `sse-status`: SSE 流式响应中推送检索/精排/生成的三阶段状态事件
- `query-rewriting`: 查询改写引擎，支持模糊扩展、口语精简明、对比拆分子查询
- `intent-routing`: 查询意图路由，L0 正则规则 + L3 LLM 兜底的混合分类
- `parent-child-chunking`: Parent-Child 层级分块，子块携带父段落上下文
- `hybrid-search`: BM25 + Dense 并行检索 + RRF 融合的混合检索引擎
- `async-upload`: 异步文档上传，支持 202 立即返回 + 后台处理 + 状态轮询
- `citation-enhancement`: 引用增强，基于 jieba 的关键词高亮 + Query-Biased Snippet 截取

### Modified Capabilities
- `retrieval-quality`: 检索配置从固定双参数扩展为可切换的混合检索模式，并集成查询改写和意图路由
- `evaluation-pipeline`: RAGAS 评估需要支持 Parent-Child 分块参数的对比实验

## Impact

- **新增依赖**: rank_bm25, jieba, Clickhouse (Docker), MinIO (Docker), Langfuse Worker
- **修改文件**: src/rag_chain.py, src/infra/vector_store.py, src/api/routes/chat.py, src/api/routes/documents.py, src/config/settings.py, src/parsers/pymupdf_parser.py, src/parsers/docx_parser.py, nginx/html/js/chat.js, nginx/html/css/style.css, docker-compose.yml
- **新增文件**: src/infra/chunk_validator.py, src/infra/chunk_enhancer.py, src/infra/bm25_index.py, src/infra/query_router.py
- **基础设施**: Langfuse 从单容器变为 4 容器 (web + worker + Clickhouse + MinIO)，Redis 配置映射调整
