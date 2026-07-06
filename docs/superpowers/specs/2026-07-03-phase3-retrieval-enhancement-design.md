# Phase 3 — 检索质量与核心增强 设计文档

## 背景

MVP 基础 RAG 链路已上线，存在以下痛点：

1. **检索候选不足**: TOP_K_RETRIEVAL=10 让 Reranker 可选范围太小，高相关但排名稍后的分块被漏掉
2. **查询理解薄弱**: 用户短查询/口语化/对比类查询在纯语义检索中效果不稳定
3. **分块粒度单一**: 所有文档用固定 chunk_size，无质量校验，坏分块直接入库
4. **表格丢失结构**: PDF/DOCX 表格被当作纯文本提取，数字数据和表头关系丢失
5. **上传无反馈**: 大文档上传后前端无进度显示，用户体验差
6. **引用不精准**: citation 只返回完整 chunk 文本，没有定位到关键词

## 变更范围

12 项改进，分 3 个 Batch：

**Batch 1 — 基础优化（配置 + 分块 + 表格 + SSE 状态）**
1. TOP_K_RETRIEVAL 默认 50
2. 分块质量校验
3. 表格提取 Markdown
4. SSE 状态推送

**Batch 2 — 检索核心（Langfuse v3 + RAGChain 拆分 + 改写 + 路由）**
5. Langfuse v3 升级
6. RAGChain 拆分
7. 查询改写
8. 意图路由

**Batch 3 — 进阶检索（Parent-Child + 混合 + 异步上传 + 引用）**
9. Parent-Child 分块
10. 混合检索
11. 异步上传
12. 引用增强

## 技术决策

### 1. RAGChain 拆分
将 `chat_with_citations()` 拆为 `search()` / `rerank()` / `stream_answer()` 三个 async 方法。好处：
- SSE 可在各阶段之间发射 status 事件
- 每步可独立 mock 测试
- 混合检索只需改造 search()，不影响上下流程

### 2. Parent-Child 分块
- 子块 256 tokens（检索粒度），父块 1024 tokens（上下文粒度）
- 子块 metadata 中 inline 存储父段落全文
- 使用现有的 RecursiveCharacterTextSplitter，不引入新分块库

### 3. BM25 词法检索
- 中文按单字切分 token（比 jieba 分词更稳定，不会因分词错误丢失召回）
- 按 kb_id 独立 pickle 持久化
- append-only 策略：删除文档时 post-search 用 MySQL doc_id 过滤

### 4. RRF 融合 (k=60)
RRF 对排名敏感而非分数绝对值，适合 BM25 + Dense 两类异构系统的结果融合。k=60 是常用推荐值（平衡头部和尾部文档的得分）。

### 5. 意图路由 L0 + L3
- L0 正则规则（零延迟），覆盖 90%+ 的常见财务查询模式
- L3 LLM 兜底（当前为 stub -> medium），保留接口供后续升级

### 6. 查询改写
改写在路由之后执行：simple 跳过，vague/medium/complex 按策略改写。
- fuzzy_short → _expand_query（取历史最后一条 user 消息前置）
- colloquial → _condense_query（去掉分析类关键词）
- compound → _decompose_query（按对比词拆分）

### 7. Citation Query-Biased Snippet
- jieba 提取查询关键词（去停用词、去单字词）
- 在 chunk 文本中定位关键词，取 ±100 字符上下文窗口
- 返回 highlights 位置列表，前端渲染 <mark> 标签

### 8. Langfuse v3 部署
- Clickhouse: 替代 PostgreSQL 存储 traces（24.12-alpine）
- MinIO: S3 兼容存储（存储附件）
- Worker: 异步处理（traces 的 post-processing）
- Web: API 服务（端口 3000，REST API 不变）

## 数据流

```
用户提问 → Intent Router → Query Rewriter
                          ↓
           ┌─ simple ──→ Direct LLM Answer
           │
           ├─ vague/medium/complex
           │       ↓
           │  Query Rewrite (expand/condense/decompose)
           │       ↓
           │  ┌─ HYBRID_SEARCH_ENABLED?
           │  │   ├─ true  → asyncio.gather(Dense, BM25) → RRF
           │  │   └─ false → ChromaDB.similarity_search
           │       ↓
           │  Reranker (top-N)
           │       ↓
           │  Parent Context Assembly
           │       ↓
           │  Prompt Building
           │       ↓
           └──→ LLM Stream → SSE (status → token → citation → done)
```

## 关键风险

| 风险 | 缓解措施 |
|------|----------|
| ChromaDB 0.5+ API 与 metadata 格式不兼容 | 验证 add() 参数，确保 chunk 用 ChunkData 而非 dict |
| BM25 pickle 跨环境不兼容 | 在部署脚本/启动时检查并重建索引 |
| L3 stub 导致非匹配查询都走 RAG | 简单问候语（"你好"）已在 short query guard 中处理 |
| Langfuse v3 Port 9000 冲突 | MinIO 容器端口 9000 映射到 host 9090 |
