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

### Requirement: abstention 由 LLM 语义判定

系统 SHALL 在检索返回空结果（dense + BM25 均无结果）时返回静态 abstention 文案；检索非空时，将 rerank 相对 top-N 的 context 全部交给生成 LLM，由 LLM 依据系统提示词（"文档中没有相关信息，请说明'未在文档中找到相关数据'"）语义判断能否回答，不因 rerank 绝对分数而提前拒绝。

#### Scenario: 检索空返回静态 abstention
- **WHEN** dense + BM25 均无检索结果
- **THEN** 生成节点返回"未在文档中找到相关数据"静态文案，不调用生成 LLM

#### Scenario: 低分 context 交由 LLM 判断
- **WHEN** 检索非空但相关 chunk rerank 分数低于 0.3
- **THEN** context 仍进入生成阶段，LLM 判断能否回答；不能回答时输出 abstention 文案而非编造
