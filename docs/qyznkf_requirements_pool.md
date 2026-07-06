# qyznkf 意向需求池

> 这是 qyznkf 项目的意向需求池，对比 financial_rag 产出的潜在改进项。
> 按领域分类，每个需求标注来源、优先级、预估成本、依赖关系。
> 决策流程：从池中取需求 → 审批 → 纳入开发计划。

## 使用方式

1. 从池中选择需求
2. 确认优先级和依赖
3. 审批通过后进入开发

---

## A. 成本控制与可靠性（高价值）

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| A-01 | LLM 限流中间件 | 防止 LLM API 调用超预算 | P0 | 低 | 无 |
| A-02 | 熔断器实现 | 对 LLM API、下游服务实现熔断。**细化**：Embedding/Rerank/LLM 各一个独立熔断器，配置不同阈值。Embedding 失败→跳过检索告知用户；Rerank 失败→用原始检索结果；LLM 失败→切备用模型或返回检索原文。连续失败 3-5 次打开熔断器，5 分钟后尝试半开恢复 | P0 | 低 | 无 |
| A-03 | **降级策略（细化）** | LLM 不可用时切到备用模型（如 Qwen-max → Qwen-turbo）；Rerank 不可用时跳过 rerank，直接用检索结果；Embedding 不可用时退化为 BM25 关键词检索（需先建倒排索引）；Langfuse 不可用时用本地 prompt 兜底。**降级矩阵**：Embedding 失败→"检索服务暂不可用"；ChromaDB 失败→"知识库暂不可用"；Rerank 失败→用原始检索顺序；LLM 失败→切备用模型或返回原文。**原则**：每个外部依赖都要有明确的降级路径，降级后告知用户"服务降级中"。不重试 401/403/400；rate limit 等待后重试；timeout 重试 1-2 次后走降级。指数退避加随机 jitter 防止惊群效应 | **P0** | 中 | A-02 |
| A-04 | Token 用量追踪与告警 | 记录每次 LLM 调用的 token 消耗，超出阈值告警 | P1 | 低 | 无 |
| A-05 | 请求超时与重试策略优化 | 对 LLM/Embedding/Reranker 调用的指数退避重试参数化 | P1 | 低 | 无 |
| A-06 | **工具调用前优化（Tool Selector）** | 调用 LLM 前从全量工具中过滤出最相关的 2-3 个再注入 Prompt，解决 Prompt 膨胀和 Token 浪费。**实现方案**：三阶段过滤——①规则优先：关键词/正则精准命中；②向量化兜底：MCP Client 启动时对所有工具 name+description 做 embedding 缓存，运行时对用户查询做 embedding 检索 Top-K；③小模型分类最终兜底。只把命中的工具注入 Prompt。适用于 Function Calling 和 MCP 两种方式 | P1 | 中 | 无 |
| A-07 | **记忆提取异步化** | 当前记忆提取（facts/preferences/corrections）是同步 inline 调 LLM，改为异步队列。对话完成后将提取任务发到 Redis List，后台 Worker 攒够阈值后批量提取并写入 SemanticMemory。**收益**：用户不感知提取延迟；批处理节省 40-50% LLM 调用成本；提取失败不影响主流程 | P1 | 低 | 无 |
| A-08 | **Langfuse 升级到 v3.x** | 当前使用 v2.95（2025.11），目标升级到 v3.x。**收益**：获得 LLM-as-Judge 评估器（代码化管理评估 prompt）、Code evaluators（Python/TS 写自动化评估）、CI/CD 集成（GitHub Actions 自动跑评估）、Experiments 实验管理。**风险**：数据库 schema 变更需迁移，自部署要规划停机窗口 | P2 | 中 | 无 |
| A-09 | **引入传统小模型（L1）** | 项目中当前没有使用 L1 级别模型（BERT 分类器/NER），纯靠规则 + L2 Embedding + L3 LLM。补充 L1 模型用于低成本的文本分类、实体提取、意图路由等场景。**收益**：替代部分 LLM 调用，降低成本；比规则更灵活。**选型建议**：BERT-base 级别的文本分类器 / NER 模型（~100MB，CPU 可跑，10-50ms/次） | P2 | 低 | 无 |

## B. 认证与安全

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| B-01 | JWT 认证 | 登录注册 + Token 鉴权，替代无认证状态 | P0 | 中 | 无 |
| B-02 | 租户隔离 | 知识库/文档/会话按租户隔离 | P0 | 高 | B-01 |
| B-03 | CORS 加固 | `allow_origins=["*"]` 改为白名单 | P1 | 低 | 无 |
| B-04 | API 请求参数校验 | 引入 Pydantic 参数校验，替换手写校验 | P1 | 低 | 无 |
| B-05 | 操作审计日志 | 记录关键操作（上传、删除、查询） | P2 | 中 | 无 |

## C. 数据库与持久层

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| C-01 | SQLAlchemy ORM | 替换手写 PyMySQL，减少 SQL 注入风险 | P1 | 中 | 无 |
| C-02 | Alembic 数据库迁移 | 自动化 schema 版本管理，支持回滚 | P1 | 低 | C-01 |
| C-03 | 数据库连接池 | 生产环境连接池管理 | P1 | 低 | C-01 |
| C-04 | 查询语句集中管理 | 目前 SQL 分散在各模块 | P1 | 低 | 无 |

## D. 代码质量与工程化

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| D-01 | Python type hints 全覆盖 | 当前几乎无类型标注 | P1 | 中 | 无 |
| D-02 | CI/CD 流水线 | GitHub Actions 自动运行测试 + lint | P1 | 低 | 无 |
| D-03 | 服务分层重构 | 引入 repository 模式，拆分 service 层 | P2 | 高 | 无 |
| D-04 | 配置管理规范化 | 环境变量集中管理，不散落在代码中 | P1 | 低 | 无 |
| D-05 | 日志结构化 | 统一日志格式，支持 request_id 串联 | P1 | 低 | 无 |
| D-06 | DDD 领域驱动重写业务层 | 按限界上下文拆分模块（知识管理/对话/检索），引入聚合根和领域事件 | P3 | 高 | D-03 |

## E. 文档处理增强

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| E-01 | 专用财务文档 chunker | 对财报、招股书等优化分块策略 | P2 | 低 | 无 |
| E-02 | **表格结构化输出** | 当前表格解析为纯文本，行列关系丢失。改为解析为 Markdown 表格格式（保留行列结构）或结构化 JSON（数值型报表），并确保不跨块切分表格。**收益**：LLM 能正确理解表格行列关系，数值检索和计算更准确 | P2 | 低 | 无 |
| E-03 | OCR 支持 | 扫描版 PDF 的 OCR 识别 | P3 | 高 | 无 |
| E-04 | **轻量实体关联** | 文档入库时用规则+LLM提取实体（公司名、人名、指标），存入 chunk 元数据。检索时从候选 chunk 提取实体→去重→拼到上下文中。不依赖 Neo4j，利用 MySQL 或元数据字段即可。**适用边界**：跨文档实体关联查询（如"这家公司对外投资了哪些子公司"）；不支持多跳图遍历，不支持关系链推理 | P2 | 中 | 无 |
| E-05 | **Token 计数分块** | 当前 CHUNK_SIZE 按字符数设置，改为按 Token 数（接入 DashScope tokenizer）。中文 512 字符约 300-400 tokens，导致 chunk 偏小。与嵌入模型对齐后分块更准确 | **P0** | 低 | 无 |
| E-06 | **分块质量自动化监控** | 入库前嵌入分块质量检查：统计块大小分布、最小块 <50 tokens 告警、乱码率 <5% 检测。当前 check_chunks.py 是 CLI 手动运行，需改为入库自动触发，碎片块自动过滤 | **P0** | 低 | 无 |
| E-07 | **Hybrid Search（BM25 + Dense 混合检索）** | 当前纯 Dense 语义检索，数值/时间/代码类精确查询匹配差。增加 BM25 稀疏检索，双路结果通过 RRF (k=60) 融合。轻量方案：rank_bm25 纯 Python 库；进阶方案：Qdrant 原生 sparse+dense。**业界 2026 年共识：Hybrid Search 是生产必备** | **P0** | 中 | 无 |
| E-08 | **Parent-Child 分块策略** | 当前单一粒度分块（512 chars）。改为 child chunks（256 tokens）精准检索 + parent chunks（1024 tokens）完整上下文。child 命中时同时返回 parent 作为 LLM 上下文。**2026 年已成为生产默认策略**，PwC 论文验证 65% 胜率、+0.2s 额外延迟 | **P0** | 中 | E-05 |
| E-09 | **文档后台处理重试机制** | 当前 `asyncio.create_task()` fire-and-forget，向量化失败（DashScope 超时/ChromaDB 写入失败）直接标记 `failed`，无重试。增加指数退避重试（初始 100ms，倍数 2，最大 5s，加随机 jitter），最多重试 2-3 次后标记 `failed`。**不重试**：解析失败（文件损坏）、扫描件检测（不可恢复）。**只重试**：网络超时、API 限流（429）、ChromaDB 临时不可用等可恢复错误 | P1 | 低 | 无 |

## F. AI / Agent 能力

| 需求 | 描述 | 来源对比 | 优先级 | 预估成本 | 依赖 |
|------|------|---------|--------|---------|------|
| F-01 | MCP 工具集成 | 接入 `mcp_server`，获取财务/税务/法律工具能力 | P1 | 低 | 无（mcp_server 已独立部署） |
| F-02 | LangGraph Agentic RAG | 从单链 RAG 升级为 Agent 编排 | P2 | 高 | F-01 |
| F-03 | **知识图谱（Neo4j）** | 搭建完整的 GraphRAG：实体提取→关系提取→ Neo4j 存储→图遍历检索。**适用边界**：需要多跳推理的场景（如"这个条款违反哪条法规""该政策影响哪些企业""同一控制下的关联方有哪些"），支持深度2-3的图遍历。**不做**：高频实时数据查询、纯数值指标查询。前置条件：需要业务中出现足量的跨文档关系查询需求，否则投入产出比不高 | P3 | 高 | E-04 |
| F-04 | **Query Rewriting（查询改写）** | 对用户原始提问改写为检索友好形式，在向量检索前增加改写节点。**改写策略按查询特征而非字数决定**：模糊短查询（<5 有效词，如"净利润"）→从对话历史/默认上下文做语义扩展；口语化长查询（如"帮我看看去年那个芯片公司赚了多少"）→语义凝练提取核心实体（"2023年 芯片公司 净利润"）；复合查询（含多个条件/对比）→查询分解为独立子查询分别检索；表述清晰的查询直接原词检索，免去改写开销。**业界效果**：MedQA +5.9% 准确率（HCQR），FinSage +24% 准确率 | **P0** | 低 | 无 |
| F-05 | **Cross-Encoder Reranker 参数调优** | 当前 TOP_K_RETRIEVAL=10, TOP_K_RERANK=5。调整：TOP_K_RETRIEVAL=**50~150**（HNSW 索引亚线性增长，扩大候选几乎不增加耗时），TOP_K_RERANK=5（Cross-Encoder 精度极高）。PwC 论文验证：候选从 10→50 后 MRR@5: 0.16→0.75。**同时注意 Lost-in-the-Middle 效应**：LLM 对 context 中间部分关注度最低，最高分 chunk 应放在 prompt 首尾位置，不要盲目增大 TOP_K_RERANK | P1 | 低 | 无 |
| F-06 | **查询分类路由（Adaptive Retrieval）** | 按查询复杂度路由：简单→直接 LLM 回答（5-10×更快）；中等→单步 RAG；复杂→多步 RAG。用规则或小模型分类器区分。**收益**：降低 30-40% 平均成本，因为大部分查询是简单的 | P1 | 低 | 无 |
| F-07 | **Retrieval 质量自评估（Self-RAG 轻量版）** | Rerank 后增加一步：用 LLM 检查检索结果是否覆盖了用户问题的所有方面。发现缺失则触发第二轮改写+检索。**边界**：最多 2 轮，防止无限循环。2026 消融研究：2 轮已捕获 95% 的 5 轮增益 | P2 | 中 | F-04 |
| F-08 | **在线 RAGAS 采样评估** | 当前 RAGAS 仅离线 CLI 运行。改为生产流量采样 5-10%，通过 Langfuse 记录后定时批量跑 RAGAS 评分，监控线上质量退化，触发告警 | P2 | 低 | 无 |

## 标签说明

**优先级：**
- P0 — 必要，缺失会影响核心使用
- P1 — 重要，有明确收益
- P2 — 有价值，但可等待
- P3 — 长期规划

**预估成本：**
- 低 — 1-3 天
- 中 — 1-2 周
- 高 — 2-4 周以上

---

## 最佳实践参考（2026 年业界共识）

以下为调研 19 个主流记忆系统项目（MemGPT/mem0/MemOS/SimpleMem/Memobase/OpenMemory 等）和 Agent 架构实践后整理的共识。

### 记忆系统

| 实践 | 说明 |
|------|------|
| **分层架构** | 95% 项目采用三层（Working/Episodic/Semantic），financial_rag 的设计符合业界主流 |
| **提取异步化** | 同步 inline 提取是最常见的错误。应使用 Buffer + 队列 + 批处理，N 条消息固定 3 次 LLM 调用，减少 40-50% 成本 |
| **记忆衰退** | 长期记忆需要衰减机制（指数衰减模型），不同认知类型不同衰减率（如事实 0.005/天，事件 0.015/天） |
| **去重合并** | 写入前做相似度检测，相同实体/事实合并而非追加 |
| **重要性过滤** | 不是所有对话内容都值得放进长期记忆，设置信度阈值（如 0.7） |

### Agent 架构

| 实践 | 说明 |
|------|------|
| **工具选择前置** | 无论用 Function Calling 还是 MCP，都应在调用 LLM 前做工具过滤，避免全量工具定义撑爆 Prompt |
| **熔断优先于限流** | 内部系统限流价值有限，熔断（快速失败）比限流（排队等）更适合 AI 场景 |
| **混合部署** | MVP 用 LangChain/Function Calling 快速验证，核心链路逐步自研 |
| **MCP 独立部署** | 工具需要跨系统复用时封装成 MCP Server，否则直接 Function Calling 写代码里 |
| **Agentic RAG 的前提条件** | 业界共识不是反对 Agentic RAG，而是强调**顺序**：①**基础检索质量达标是前提**——Hybrid Search + Good Chunking + Cross-Encoder Reranker 必须先做好。在这些没做之前上 Agentic RAG，等于在垃圾数据上多花 5-10x token 反复检索，结果仍是错的；②**使用 adaptive routing**——简单查询走单步 RAG（低成本），复杂/多跳查询才走 Agentic 多步推理（按需付费），不需要所有查询都扛 Agentic 的 overhead；③**硬工程约束不可少**——循环上限（建议 max 3 轮）、上下文裁剪（≈6 docs × 800 tokens）、每步可观测；④**场景边界**：用户接受 30s+ 延迟的场景（金融研究/法律分析）适用；需要实时交互的场景（客服/IDE 助手）不适用 |

### Prompt 管理

| 实践 | 说明 |
|------|------|
| **运行时管理优于 Git 管理** | Prompt 不是代码，不应和代码混在 Git 中。回退 prompt 会牵连代码，给产品开 Git 权限是安全红线。用 Langfuse 做统一管理更合适 |
| **Langfuse 作为唯一权威来源** | prompt 的编辑、版本管理、A/B 测试、操作审计全部走 Langfuse。开发、产品、运营各角色在各自权限内操作，互不干扰 |
| **本地兜底只做离线保护** | 代码中的兜底 prompt 只应对 Langfuse 宕机场景，不参与日常迭代。版本号与 Langfuse 同步，标注最后同步时间 |
| **避免双轨制** | 不需要 Git 也存一份 prompt。当 Langfuse 和 Git 不一致时会产生混淆，最终没人知道哪个是"对的" |

### 可靠性（限流/熔断/降级）

RAG 管线中需要防护的关键位置：

```
用户请求 → 网关限流 → Embedding → 向量库 → Rerank → LLM 生成 → 返回
                         ↓熔断降级   ↓超时降级 ↓熔断跳过  ↓熔断+降级
```

| 实践 | 说明 |
|------|------|
| **每层都有超时** | Embedding（500ms）、向量库查询（5s）、Rerank（5s）、LLM（30s）。超时是熔断的前置条件 |
| **限流用在 API 入口和 LLM 调用** | API 入口限流防滥用，LLM 调用限流控成本。内部系统限流价值有限，熔断比限流更重要 |
| **熔断用在外部依赖** | LLM 服务、Rerank 服务、Embedding 服务分别设置熔断器。连续失败 3-5 次打开熔断器，5 分钟后尝试半开恢复 |
| **降级需要分层策略** | Embedding 不可用→退 BM25；Rerank 不可用→跳过；主 LLM 不可用→切备用模型或返回原文 |
| **不重试所有错误** | 401/403/400 不重试；rate limit 等待后重试；timeout 重试 1-2 次后走降级 |
| **重试用指数退避 + jitter** | 初始 100ms，倍数 2，最大 5s。加随机 jitter 防止惊群效应 |
| **降级后告知用户** | "检索服务暂时异常，结果可能不完整"。不要返回通用的 500 或空结果 |
| **Chaos Engineering** | 在测试环境主动注入故障（停掉 Embedding、让 LLM 超时），验证降级路径生效 |

**当前 qyznkf 现状**：
- ✅ ChatManager：Redis→内存降级
- ✅ PromptManager：Langfuse→本地兜底
- ✅ 模型调用：@with_retry 指数退避重试
- ❌ LLM 熔断（A-02，P0）
- ❌ LLM 限流（A-01，P0）
- ❌ Rerank/Embedding 降级（A-03，P1）

### 知识图谱

| 实践 | 说明 |
|------|------|
| **先轻量后重量** | 先在 chunk 元数据存实体关联（方案 A），确认有跨文档关系查询需求后再上 Neo4j |
| **不要一步到位** | 财报问答场景向量检索已足够，知识图谱收益主要体现在多跳推理和关系类问题 |
| **异步提取** | 图谱提取同样应走后台异步队列，避免阻塞文档上传流程 |

### RAG 全链路增强（新增 — 2026 年业界共识）

#### 检索前（Pre-Retrieval）

| 实践 | 说明 |
|------|------|
| **Query Rewriting（查询改写）** | 用 LLM 或小模型将用户口语化查询转写为检索友好形式。生产验证中改写后命中率提升 10-20%。HyDE（Hypothetical Document Embeddings）是特殊形式的改写——先生成假设性理想回答再检索，zero-shot 场景 recall +15-20% |
| **Query Decomposition（查询分解）** | 将复杂多面问题拆解为独立子查询（如"公司2023年营收和利润"→"2023年营收"+"2023年利润"），分别检索后合并。FinSage 系统（CIKM 2025）使用此技术，Recall 达 92.51% |
| **Multi-HyDE（多样化假设检索）** | 生成多个不等价的假设查询（非同义词变体），覆盖不同信息维度。EMNLP 2025 论文验证：准确率 +11.2%，幻觉 -15% |
| **Metadata Filtering（元数据过滤）** | 检索前按文档类型、日期、安全级别等过滤，大幅减少噪声。Red Hat OGX 方案推荐三级过滤层：向量库级 / 文档级 / 分块级 |
| **查询分类路由** | 按复杂度路由查询：简单→直接 LLM 回答（5-10×更便宜），中等→单步 RAG，复杂→多步 RAG。可降低 30-40% 平均成本 |

#### 检索（Retrieval）

| 实践 | 说明 |
|------|------|
| **Hybrid Search（混合检索）** | BM25 稀疏检索 + Dense 向量语义检索，通过 RRF（Reciprocal Rank Fusion, k=60）融合。PwC 2025 论文验证：金融文档 68% 胜率。**2026 年生产必备** |
| **Parent-Child 分块（Small-to-Big）** | **2026 年生产默认策略**。Child chunks（128-256 tokens）精准检索 + Parent chunks（512-1024 tokens）完整上下文。PwC 论文验证 65% 胜率，只增加 0.2s 延迟。**NVIDIA 实测：accuracy 从 61% 提升至 89%**。LangChain 的 ParentDocumentRetriever 和 LlamaIndex 的 HierarchicalNodeParser 都有原生支持 |
| **Semantic Chunking（语义分块）** | 按句子/段落边界切分而非固定字符数，保持语义完整性。与固定分块比效果提升 18-27%（QASC 论文，Apr 2026） |
| **Multi-Path Retrieval（多路径检索）** | BM25 + Dense + Metadata + HyDE 四路并行检索，结果融合。FinSage 系统以此实现 92.51% Recall |
| **Contextual Retrieval（上下文检索）** | 索引时用 LLM 为每个 chunk 生成 50-100 token 摘要拼接到 chunk 上再 embedding。**Anthropic 验证：结合 reranker 可减少 67% 检索失败**。EACL 2026 基准一致验证提升检索质量 |

#### 检索后（Post-Retrieval）

| 实践 | 说明 |
|------|------|
| **Cross-Encoder Reranking** | **生产必备，不可跳过**。对 top-50~150 候选做交叉编码精排后保留 top-5~20。PwC 论文验证 MRR@5: 0.16→0.75 |
| **Lost-in-the-Middle 防御** | LLM 对 context 中间部分 recall 最低，首尾最高。将最高分 chunk 放在 prompt 首尾位置，次要 chunk 放中间。不要盲目增大 TOP_K_RERANK——更多 chunk 不一定更好，反而可能稀释核心信息 |
| **Adaptive Context Expansion** | 非无脑宽窗口，而是选择性扩展相邻 chunk。SCAR（June 2026）：Recall 92.8%，上下文 tokens 减少 27.1% |
| **Self-RAG / Corrective RAG** | 自评估检索结果相关性——正确则生成，错误则 web 搜索 fallback。Self-RAG 使用 [Retrieve]/[IsRel]/[IsSup]/[IsUse] 反射 token 做自评估。CRAG 使用 Retrieval Evaluator 做三分类（Correct/Incorrect/Ambiguous） |
| **Post-Retrieval Cascade** | "最便宜优先"级联策略：按成本升序执行流程步，只有需要时才升级到 LLM。Coverage Illusion 论文（May 2026）验证：72.2% 查询无需 LLM 增强，延迟降低 31.8% |
| **Context Compression** | 压缩/摘要检索结果后送入 LLM，节省 30-50% context tokens |

#### 评估与监控

| 实践 | 说明 |
|------|------|
| **三层独立评估体系** | 检索层（Recall@k, nDCG, MRR, Hit Rate）→生成层（Faithfulness, Answer Relevancy, Groundedness）→归因层（ChunkAttribution 回答是否引用了chunk, ChunkUtilization 模型对chunk的使用效率）。**不要只评估最终回答**，三层指标反映不同维度的失败模式 |
| **RAGAS 四指标** | Faithfulness（忠实度）、Answer Relevancy（回答相关性）、Context Precision（上下文精度）、Context Recall（上下文召回率）。**不要聚合为单一分数**——每个指标反映不同维度的失败模式 |
| **生产监控黄金信号** | TTFT（首 token 延迟）、TPOT（输出 token 速率）、单次请求成本、保卫命中率（Guardrail）、用户隐式反馈（编辑距离/会话放弃率） |
| **Span 级追踪** | 每个 RAG 请求展开为检索 span（chunks + rerank score + latency）→ 生成 span（model + tokens + cost）→ 保卫 span → 路由 span。工具：Langfuse（OSS，自部署）、Arize Phoenix |
| **CI/CD 门禁** | Golden set（100-500 条，按意图分层）每次 PR 跑 RAGAS，按指标阈值卡口（如 faithfulness ≥ 0.90）。每季度轮换 10-20% 防止分布漂移 |
| **Embedding Drift（嵌入漂移）检测** | 三种漂移：换 embedding 模型未重索引（model drift）；新文档类型改变向量空间（corpus drift）；用户查询词汇变化（query drift）。版本化所有组件，监控 MRR 随时间变化，指标下降超过阈值触发告警 |
| **Stale Context（上下文过期）** | 索引数据可能随时间过期或不一致。需要 corpus 版本化（hash + date + chunk count），每次更新知识库后自动重新评估检索质量 |
| **在线采样评估** | 5-20% 生产流量在线评分（100% 采样异常请求）。用蒸馏小模型做 judge（~50ms 级）替代 LLM judge 降低成本 |
| **Judge 模型校准** | **永远不要信任未校准的 LLM judge**。在 ≥50-100 样本上与人工标注校准，固定 judge prompt 和模型版本，使用与生成模型不同的模型族避免 self-bias |

### 文档解析

| 实践 | 说明 |
|------|------|
| **80% 的 RAG 失败源于 ingestion 层（非 LLM）** | PremAI/ DigitalOcean 2026 调研结论：**"80% of RAG failures trace back to the ingestion and chunking layer, not the LLM"**。解析质量直接决定 RAG 系统的**上限**，检索策略决定**下限**。投入精力优化解析管线的性价比远高于调 LLM prompt |
| **解析是 RAG 的瓶颈** | 错误解析（表格结构破坏、乱码、语义截断）会直接污染知识库 |
| **表格不跨块切分** | 表格应作为独立整体处理，绝不跨块切分。基础做法：解析为 Markdown 表格；进阶做法：解析为结构化 JSON（数值型报表） |
| **解析管线容错** | 解析失败≠拒绝入库。应有降级策略：优先解析器失败后改用备用解析器或全文文本兜底，让更多有效数据进入知识库 |
| **解析质量采样校验** | 在 Chunking 阶段做采样校验：检查乱码率（<5%）、块大小分布、最小块大小（>50 Token）。提前发现低质量数据，避免批量入库污染 |
| **扫描件用 OCR 兜底** | 检测为扫描件后启用神经网络 OCR（Tesseract 4.x+ / PaddleOCR）。关键文档建议双 OCR 引擎交叉校验，数值型文档做数值一致性校验 |
| **文档类型专用策略** | PDF→Layout-aware Parser；Markdown→按标题层级切分；Word→按样式重建文档树；Excel→按行/区域提取为 JSON |

### 表格提取

主流 PDF 表格提取库对比（基于 2026 年实测金融财报数据）：

| 工具 | 方式 | 复杂表格能力 | 财务报告表现 | 速度 | 成本 |
|------|------|------------|------------|------|------|
| **pdfplumber** | Python 库 | ⭐⭐⭐⭐ | 高准确率 | 中等 | 免费 |
| **PyMuPDF** (fitz) | Python 库 | ⭐⭐⭐ | 中等 | 最快 | 免费 |
| **Camelot** | Python 库 | ⭐⭐ | 复杂报表直接失败 | 中等 | 免费 |
| **Tabula** | Java 包装 | ⭐⭐ | 格式问题多 | 慢 | 免费 |
| **MinerU** | 独立服务 | ⭐⭐⭐⭐⭐ | 高（含 OCR+VLM） | 慢 | 免费+GPU |
| **Unstructured** | 企业服务 | ⭐⭐⭐⭐ | 好 | 中等 | 免费/付费 |
| **LLMWhisperer** | 云端 API | ⭐⭐⭐⭐⭐ | 最高 | 快 | 付费 |

**实践建议：**
| 实践 | 说明 |
|------|------|
| **主用 pdfplumber** | 财务类复杂表格效果最好，纯代码替换，不需要额外部署服务 |
| **MinerU 兜底** | pdfplumber 提取失败的扫描件或极端复杂排版时用，需 GPU |
| **表格不跨块切分** | 表格应作为独立整体处理，绝不跨块。先转为 Markdown 表格或 JSON 再入库 |
| **数值校验** | 财务报表的数字提取后做一致性校验（合计=分项之和），发现不一致标记为解析异常 |

### 分块策略

| 实践 | 说明 |
|------|------|
| **Token 计数优于字符计数** | 中文 1 字符约 1-2 tokens，用字符数设 chunk_size 会导致与嵌入模型 Token 限制不匹配。必须接入 tokenizer 做精确计数 |
| **chunk_size 甜点区间 256-512 tokens** | 过小（<50）导致语义碎片化，过大（>2500）导致信息稀释。财报等分析型场景可偏大（512-1024）。2026 年多个基准测试中 512-token 分块以 69% 准确率排名第一 |
| **分块策略选择 = 文档类型 + 查询类型** | 事实查询→偏小（64-256）；分析推理→偏大（512-1024）；技术文档→按标题层级；代码→按函数块 |
| **文档类型特异性分块** | 不同类型文档需要不同策略：合同/法规→**clause/章节级**（语义单元是条款）；论文/长文→**语义分块**（主题边界优于 markup）；FAQ/产品文档→**固定 512 + 重叠**（段落均匀）；代码→**late-interaction**（ColBERT 级 token 匹配保留标识符）；多语言→**late-interaction**（跨语言 token 级匹配） |
| **分块质量采样校验** | 入库前自动检查：块大小分布是否均匀、最小块是否 <50 tokens（碎片告警）、乱码率 <5% |
| **Parent-Child 分块是生产升级路径** | 小块精准检索 + 大块完整上下文，兼顾精度与生成质量。**2026 年已成为生产默认策略**，当 RAGAS Context Recall <80% 时优先考虑 |
| **语义分块** | 按句子/段落边界而非固定字符数切分。QASC 论文（Apr 2026）验证对比固定分块 F1 提升 18-27% |
| **Late Chunking（延迟分块）** | 2025-2026 主流技术：先对整个文档做 embedding，再按分块范围 pooling，保留跨块语义。需专用 embedding 支持（如 jina-embeddings-v3） |
| **上下文关联索引** | 索引时将 LLM 生成的摘要/doc 级信息拼接到每个 chunk 上，使每个 chunk 拥有文档级上下文。EACL 2026 基准：一致提升检索质量 |

---

---

## 实施路线图

基于 2025-2026 年业界调研和投入产出比分析，建议分四个阶段推进：

### 阶段 1：检索质量奠基（P0，1-2 周）

| 顺序 | 需求 | 原因 |
|------|------|------|
| 1 | E-05 Token 计数分块 | 基础对齐，后续所有检索优化的前提 |
| 2 | E-07 Hybrid Search（BM25 + Dense） | 最大单点收益，精确查询命中率显著提升 |
| 3 | E-06 分块质量自动化监控 | 不让坏数据进知识库 |
| 4 | E-08 Parent-Child 分块 | 兼顾检索精度与生成上下文质量 |
| 5 | A-02/A-03 熔断+降级 | 生产可靠性的基础保障 |
| 6 | F-04 Query Rewriting | 提升口语化查询匹配率 |

### 阶段 2：核心能力增强（P1，2-4 周）

| 顺序 | 需求 | 原因 |
|------|------|------|
| 7 | F-05 Cross-Encoder Rerank 参数调优 | 极低成本获精度提升（MRR 0.16→0.75） |
| 8 | F-06 查询分类路由 | 降本增效，简单查询不走完整 RAG |
| 9 | A-04 Token 用量追踪 | 成本可观测 |
| 10 | A-06 Tool Selector | 工具调用前过滤，降低 Prompt 膨胀 |
| 11 | A-07 记忆提取异步化 | 减少用户感知延迟，降低 40-50% 成本 |
| 12 | E-02 表格结构化输出 | 金融场景高频需求 |
| 13 | E-04 轻量实体关联 | 跨文档关系查询基础 |

### 阶段 3：数据驱动闭环（P2，4-6 周）

| 顺序 | 需求 | 原因 |
|------|------|------|
| 14 | F-07 Retrieval 质量自评估 | 自动修复检索失败 |
| 15 | F-08 在线 RAGAS 采样评估 | 数据驱动的 RAG 优化闭环 |
| 16 | A-08 Langfuse v3.x 升级 | 获得 LLM-as-Judge 评估器等高级功能 |
| 17 | E-01 专用财务文档 chunker | 按文档类型优化分块 |
| 18 | A-09 引入传统小模型（L1） | 降低 LLM 调用依赖 |

### 阶段 4：智能 Agent 化（P3，6 周+）

| 顺序 | 需求 | 原因 |
|------|------|------|
| 19 | F-01 MCP 工具集成 | 接入 mcp_server 获取专业工具 |
| 20 | F-02 LangGraph Agentic RAG | 从单链升级为 Agent 编排。**前提条件**：阶段 1-2 的基础检索质量已达标（Hybrid Search + Parent-Child Chunking + Reranker 就位），且经评估确认有足量需要多跳推理的复杂查询。Agentic RAG 不修复坏检索——只会用更多 token 在同样差的检索结果上反复折腾。配合 adaptive routing 使用：简单查询走单步 RAG，复杂查询才走 Agentic 多步推理 |
| 21 | F-03 知识图谱（Neo4j） | 深层多跳推理（需前置条件确认） |
| 22 | E-03 OCR 支持 | 扫描件 PDF 支持 |

---

## 项目涉及模型分类参考

以下梳理本项目（qyznkf + financial_rag）和对话中涉及的所有模型，按层级分类。

### 四层模型体系

| 层级 | 代表 | 参数量 | 成本 | 推理位置 | 适合做什么 |
|------|------|-------|------|---------|----------|
| **L0 规则/字典** | 正则表达式、jieba 分词、关键词匹配、已知公司名单 | 无 | 免费 | 本地 CPU | 日期/金额/百分数提取、公司名匹配、意图关键词匹配 |
| **L1 传统小模型** | BERT 分类器、NER 模型、LightGBM 分类器 | 10-500 MB | 极低（毫秒级，免费） | CPU 可跑 | 文本分类（工具选择）、命名实体识别（公司/人名提取）、意图路由 |
| **L2 Embedding/Rerank** | text-embedding-v3 (DashScope)、bge-reranker (SiliconFlow) | 几百 MB | 低（按 API 调用计费，远低于 LLM） | CPU/GPU | 语义相似度、工具向量检索、Rerank 排序 |
| **L3 大语言模型（LLM）** | 见下表 | 7B ~ 几百 B | 高（按 Token 计费） | GPU | 推理、生成、复杂理解 |

### L3 大语言模型细分

| 子类 | 参数量 | 代表 | 项目中使用 | 适合 |
|------|-------|------|----------|------|
| **小参数 LLM**（你的理解） | 7B-14B | Qwen2.5-7B, DeepSeek-7B, GLM-4-9B | 可以用作本地部署的 fallback | 基础推理、简单 RAG |
| **中参数 LLM** | 32B-72B | Qwen2.5-72B, DeepSeek-V2 | 可本地部署，需多 GPU | 复杂推理、Agent 场景 |
| **大参数 LLM（云端）** | 数百 B ~ T 级 | Qwen-max（DashScope）、GPT-4o、DeepSeek-V3 | **qyznkf 当前主力：Qwen-max via DashScope** | 主 RAG 链路、Agent 编排、评估 |

### 本项目中各场景的模型选型

| 场景 | 当前使用的模型（qyznkf） | 可用的更优方案 |
|------|------------------------|--------------|
| **主 RAG 推理** | DashScope Qwen-max（云端大参数 LLM） | 保持不动，这是主力 |
| **Embedding（向量化）** | DashScope text-embedding-v3（L2 Embedding） | 保持不动 |
| **Rerank（重排序）** | DashScope gte-rerank-v2（L2 Rerank） | 保持不动 |
| **工具/模式选择（A-06）** | 无（计划做：规则→L2 Embedding→L1 BERT 分类器） | 三阶段过滤 |
| **实体提取（E-04）** | 无（计划做：规则提取 + 可选 L1 BERT NER） | 先规则，不够再加 BERT NER |
| **文档分块 Token 计数（E-05）** | 无（计划做：接入 DashScope tokenizer） | tokenizer 本身就是字典查找，免费 |
| **RAGAS 评估** | DashScope Qwen-max（云端大参数 LLM） | RAGAS 内部调 LLM 算 faithfulness 等指标，保持不动 |
| **Langfuse LLM-as-Judge（A-08）** | 无（计划做：升级后使用） | 建议用小参数的 Judge 模型（如 GPT-4o-mini 级别），降低成本 |

### 关键结论

- **L0 规则**：能做的就不要上模型，免费且精确
- **L1 传统小模型**：当前项目中**没有用到**，如果后续需要（如 NER 实体提取、文本分类器），建议用 BERT 级别的（~100MB），CPU 可跑
- **L2 Embedding/Rerank**：你已经用上了（DashScope text-embedding-v3 / gte-rerank-v2），这是正确的选择
- **L3 LLM 小参数（7B-14B）**：项目中**没有用到**，主要因为 DashScope 的 Qwen-max 效果更好。后续如果需要本地 fallback 可以考���
- **L3 LLM 大参数（云端）**：你一直在用（Qwen-max），项目的核心依赖
