## MODIFIED Requirements

### Requirement: Retrieval parameter configuration
The system SHALL keep `TOP_K_RETRIEVAL` and `TOP_K_RERANK` configurable via environment variables in `src/config/settings.py`, defaulting to 30 and 5 respectively.

The system SHALL support overriding these values at evaluation time without modifying source code.

`TOP_K_RETRIEVAL` SHALL be treated as the candidate pool size fed into rerank, not as a final context size; the effective number of chunks entering the model SHALL be bounded by `TOP_K_RERANK` downstream.

#### Scenario: Parameter override via environment
- **WHEN** user sets `TOP_K_RETRIEVAL=15` and `TOP_K_RERANK=8` in `.env`
- **THEN** the RAG pipeline SHALL use 15 initial retrieval results and keep 8 after reranking

#### Scenario: 候选池不构成最终上下文条数
- **WHEN** 候选池配置大于 `TOP_K_RERANK`
- **THEN** 进入模型的 chunk 条数 SHALL 不超过 `TOP_K_RERANK`，候选池大小只影响精排的输入

### Requirement: Cross-document aggregation
When a query requires information spread across multiple chunks from different documents within the same KB, the RAG chain SHALL aggregate context from up to TOP_K_RERANK chunks regardless of which document they originate from.

The selection of those chunks SHALL be decided by rerank relevance, not by a per-document quota: multiple chunks originating from the same document SHALL remain eligible when they are the most relevant candidates.

#### Scenario: Cross-document query returns aggregated results
- **WHEN** user asks a question whose answer spans multiple documents in the same KB
- **THEN** the response SHALL include information from all relevant documents, with citations tracing back to each source document

#### Scenario: 单文档多片段不被截断
- **WHEN** 一个问题需要同一文档的多个不同片段（如跨章节的指标与说明）
- **THEN** 该文档的多个片段 SHALL 能同时进入精排候选，且不因"每文档配额"被提前丢弃

### Requirement: Retrieval quality comparison
The system SHALL provide a CLI command to compare retrieval quality across different parameter combinations:
- TOP_K_RETRIEVAL: 10, 30, 50
- TOP_K_RERANK: 3, 5, 8

Results SHALL include average relevance score and recall@K metrics per combination.

#### Scenario: Retrieval comparison report
- **WHEN** user runs retrieval comparison CLI
- **THEN** a comparison table SHALL be printed showing metrics per parameter combination

### Requirement: abstention 决策路径（全模型决策）

删除 classify 节点与固定流水线后，系统 SHALL 将空检索的 abstention 触发全部改为"模型决策"：`retrieve_kb` 返回空结果（或证据不足）时作为普通工具结果回喂模型，模型 SHALL 基于证据情况自行选择——输出 abstention 文案、调用 `ask_user` 追问、调用 `escalate_to_human` 转人工、调用 `search_web` 联网补充、或基于已有证据作答。不再存在"确定性 abstention 分支"。

`retrieve_kb` SHALL 只检索会话绑定的那一个知识库；不存在按 query 与 KB 元数据相似度自动选库的路径。

#### Scenario: 空检索模型决策
- **WHEN** retrieve_kb 返回空结果
- **THEN** 模型收到空工具结果后自行决定 abstain / 追问 / 转人工 / 联网搜索，不由流水线硬编码判断

#### Scenario: 检索不相关 → 换词再检 → 联网兜底
- **WHEN** retrieve_kb 结果为空或全部明显不相关
- **THEN** 模型 SHALL 提炼核心实体换一种问法再次检索；仍无相关结果时 SHALL 调用 search_web 联网搜索补充，回答先说明"该问题不在当前知识库范围内"；web 搜索关闭或失败时走纯拒答

#### Scenario: 未绑定 KB 时不检索
- **WHEN** 会话未绑定知识库（`kb_id` 为空）
- **THEN** retrieve_kb SHALL 直接返回空结果，不触发跨库检索

## REMOVED Requirements

### Requirement: 检索去重策略参数化

**Reason**: 该要求与 `retrieval-judgment` 的「检索结果去重」定义的是**同一条事实**（检索去重的口径与位置），属重复 owner —— 正是 ADR-0003 明令禁止的"同一条规则写在多处"。且其口径"按 `doc_id` 去重、每文档保留条数可配置（默认 1）"已被 ADR-0001 推翻，改为按内容（父块）去重。去重口径的唯一 owner 归 `retrieval-judgment`。

**Migration**:

- 去重的口径、位置与全部场景断言，由 `retrieval-judgment` 的「检索结果去重」承担，本变更已在该处给出完整的新版本。
- `RETRIEVAL_MAX_PER_DOC` 随之退出（不再存在"每文档保留条数"这一可配置维度）；实测的存量值语义仍在 `[retrieval] retrieve replay` 事件行中留有历史记录。
- `src/cli/compare_dedup.py` 的 A/B 网格 `{1,2,3}` 针对该已废口径，需同步改造为内容级或标记作废。
- 引用本条历史的文档（`docs/agents/glossary.md` 的 `dedup` / `RETRIEVAL_MAX_PER_DOC` 词条）需一并改写。
