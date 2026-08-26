## ADDED Requirements

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
