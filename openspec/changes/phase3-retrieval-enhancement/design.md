## Context

MVP 阶段的基础 RAG 链路使用固定 TOP_K_RETRIEVAL=10 和 TOP_K_RERANK=5 的参数配置，纯 Dense 向量检索 + Reranker 精排。实际使用中发现：

- 10 个检索候选不足以让 Reranker 充分排序，部分高相关分块因向量相似度略低被漏掉
- 用户查询表达不规范（短查询、口语化、对比类），纯语义检索效果不稳定
- 分块粒度单一且无质量校验，坏分块（乱码、过短）直接入库影响检索质量
- 文档表格无法识别，表格中的财务数字被当作纯文本丢失了结构信息
- 上传大文档时前端无进度反馈，用户体验差

## Goals / Non-Goals

**Goals:**

- 将检索候选从 10 提升至 50，使 Reranker 能充分排序
- 入库前自动校验分块质量，拦截异常分块
- PDF/DOCX 表格转为 Markdown，保留表格结构信息
- 流式响应中推送检索→精排→生成的阶段状态
- 支持 4 种查询改写策略，提升非标查询的检索准确率
- 基于规则的意图路由，simple 查询走直接回答、vague/medium/complex 走 RAG
- Parent-Child 分层分块，子块检索 + 父块上下文
- BM25 词法 + Dense 语义并行混合检索 + RRF 融合
- 异步上传：202 立即返回 + 后台处理 + 状态轮询 + 分块预览
- jieba 分词 + Query-Biased Snippet 提升引用可读性
- Langfuse 升级到 v3 以获取新版功能和稳定支持

**Non-Goals:**

- 不支持 OCR（扫描件检测已有但仅告警）
- 不涉及多轮对话的意图记忆（仅单轮查询改写）
- 不做跨知识库的混合检索（BM25 按 kb 隔离）
- 不引入全文检索引擎（Elasticsearch 等）
- 不做前端重写（仅在原有 JS/CSS 上增量修改）

## Decisions

1. **RAGChain 拆分为三个方法** — 将原来单一的 chat_with_citations() 拆为 search() / rerank() / stream_answer()，便于 SSE 分阶段推送状态，也方便各环节独立测试和替换
2. **Parent-Child 用 RecursiveCharacterTextSplitter** — 无需引入新的分块库，复用已有的 LangChain 工具，按 token 数递归切分
3. **BM25 按字符切分 token** — 中文不适合英文的按词切分策略，直接按单字切分作为 BM25 token，简单有效
4. **RRF k=60 融合 BM25 + Dense** — RRF 对排名敏感而非分数绝对值，适合两类异构检索结果的融合。k=60 是常见推荐值
5. **BM25 索引持久化为 pickle** — 按 kb_id 独立子目录存储，不需要每次重启重建。删除文档时后过滤（MySQL doc_id 检查），不做索引删除
6. **意图路由 L0 优先于 L3** — 正则规则零延迟，覆盖常见财务查询模式。L3 LLM 兜底保留接口签名但当前为 stub（返回 fallback="medium"）
7. **查询改写在路由之后执行** — 先分类再改写，simple 查询跳过改写步骤减少开销
8. **SSE 状态事件不改变 token 流格式** — 新增 event type "status"，token 事件保持原样，前端仅多监听一个事件类型
9. **Citation 改用 jieba 分词的 Query-Biased Snippet** — 对中文财务文本，jieba 比空格分词精准。返回 snippet + highlights 位置，前端渲染 <mark> 标签
10. **Langfuse v3 用 REST API 不变** — API 层兼容，仅部署架构变化：Clickhouse 存 traces、MinIO 存附件、Web 和 Worker 分离

## Risks / Trade-offs

- [BM25 按字切分] 对比按词切分的 BM25，字级 BM25 可能引入更多噪声，但中文分词的不确定性会引入更大的召回偏差
- [pickle 持久化] pickle 格式不跨 Python 版本、不跨架构（如 x86→ARM），部署环境变更时需要重建索引
- [L3 LLM 兜底为 stub] 当前使用固定 fallback="medium"，意味着所有未命中规则的查询都走 RAG 检索，对简单问候类查询造成不必要的开销
- [ChromaDB 版本兼容] 异步上传中的 chunk metadata 格式与 ChromaDB 0.5+ API 兼容性需要验证，`add()` 方法在 v0.5+ 中参数变化较大
