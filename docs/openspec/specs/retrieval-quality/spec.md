# retrieval-quality Specification

## Purpose
TBD - created by archiving change mvp-core-features. Update Purpose after archive.
## Requirements
### Requirement: Retrieval parameter configuration
The system SHALL keep `TOP_K_RETRIEVAL` and `TOP_K_RERANK` configurable via environment variables in `src/config/settings.py`, defaulting to 10 and 5 respectively.

The system SHALL support overriding these values at evaluation time without modifying source code.

#### Scenario: Parameter override via environment
- **WHEN** user sets `TOP_K_RETRIEVAL=15` and `TOP_K_RERANK=8` in `.env`
- **THEN** the RAG pipeline SHALL use 15 initial retrieval results and keep 8 after reranking

### Requirement: Short query handling
The system SHALL handle short queries (under 5 Chinese characters) gracefully by:
- Returning a fallback response indicating the query is too short
- Not performing an empty or meaningless vector search

#### Scenario: Short query returns guidance
- **WHEN** user sends a query of fewer than 5 Chinese characters (e.g., "你好", "是的")
- **THEN** the system SHALL respond with a message suggesting a more specific financial question

### Requirement: Cross-document aggregation
When a query requires information spread across multiple chunks from different documents within the same KB, the RAG chain SHALL aggregate context from up to TOP_K_RERANK chunks regardless of which document they originate from.

#### Scenario: Cross-document query returns aggregated results
- **WHEN** user asks a question whose answer spans multiple documents in the same KB
- **THEN** the response SHALL include information from all relevant documents, with citations tracing back to each source document

### Requirement: Retrieval quality comparison
The system SHALL provide a CLI command to compare retrieval quality across different parameter combinations:
- TOP_K_RETRIEVAL: 5, 10, 15
- TOP_K_RERANK: 3, 5, 8

Results SHALL include average relevance score and recall@K metrics per combination.

#### Scenario: Retrieval comparison report
- **WHEN** user runs retrieval comparison CLI
- **THEN** a comparison table SHALL be printed showing metrics per parameter combination

### Requirement: 删除 grader 与重试环

系统 SHALL 移除 grader 关键词覆盖度评分与检索重试环，工作流 SHALL 为 `retrieve → rerank` 直连。不再存在 `grader` 图节点、`route_by_grader` 条件边、`RetrievalGrader` 评分器，以及 `grader_score`/`retrieval_retries`/`downgraded`/`downgrade_reason`/`_prev_rewritten_query` 状态字段。

#### Scenario: 图无 grader 节点
- **WHEN** 编译工作流时检查节点注册
- **THEN** 节点列表不包含 grader，检索结果直接进入 rerank

#### Scenario: 无 grader 状态残留
- **WHEN** 请求完成时检查状态
- **THEN** AgentState 不包含 `grader_score`/`retrieval_retries`/`downgraded` 等字段，且请求只执行一次检索和一次 rerank

### Requirement: abstention 决策路径（全模型决策）

删除 classify 节点与固定流水线后，系统 SHALL 将空检索的 abstention 触发全部改为"模型决策"：`retrieve_kb` 返回空结果（或证据不足）时作为普通工具结果回喂模型，模型 SHALL 基于证据情况自行选择——输出 abstention 文案、调用 `ask_user` 追问、调用 `escalate_to_human` 转人工、或基于已有证据作答。不再存在"确定性 abstention 分支"。

#### Scenario: 空检索模型决策
- **WHEN** retrieve_kb 返回空结果
- **THEN** 模型收到空工具结果后自行决定 abstain / 追问 / 转人工，不由流水线硬编码判断

#### Scenario: KB 未解析 → 语义选库检索
- **WHEN** kb_router 未解析出知识库（如无 user_id）
- **THEN** retrieve_kb 以语义匹配 query 与各 KB 的 name+description，选中相似度最高的 1 个知识库进行检索；匹配失败（无 KB 或相似度低于阈值）时返回空工具结果，模型按 abstain / ask_user / escalate 决策，不触发旧确定性 abstention 文案

### Requirement: Context rendering includes entity metadata

The system SHALL render entity metadata (company / report_period / sec_code / person / currency / report_type) into the retrieval context shown to the LLM, via `RAGContext.to_prompt_text()`. Only entities that exist SHALL be rendered, in the order defined by `ENTITY_RENDER_ORDER`.

The rendered format SHALL be shared between production prompt generation and RAGAS NLI evaluation context, so both see identical context.

#### Scenario: Context with entities
- **WHEN** a retrieved chunk has `entities={company: 东软集团, report_period: 2025年第一季度}` and is included in `format_context`
- **THEN** the resulting prompt context SHALL include both entity fields alongside source/page/content

#### Scenario: Context without entities
- **WHEN** a retrieved chunk has an empty entities dict
- **THEN** the prompt context SHALL contain only source/page/content, matching the previous format exactly

### Requirement: Rerank context passthrough

The system SHALL carry entity metadata from ChromaDB chunk metadata through the rerank stage into `RAGContext.entities`.

#### Scenario: Entities survive rerank
- **WHEN** a chunk with `company`/`report_period` metadata is returned by rerank
- **THEN** the corresponding `RAGContext` SHALL expose those values via its `entities` dict
