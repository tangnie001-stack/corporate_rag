## Why

当前查询改写（`rewrite_node`）是纯规则：medium 用 `expand_query` 盲拼接上轮消息，complex 用 `decompose_query` 机械切分。系统性调查发现"反复追问/abstention"的三层根因：

1. **改写盲拼接**：`腾讯2024年营收多少 毛利率呢` 稀释查询，rerank 仅 0.18。
2. **表述鸿沟（query-document gap）**：文档报告期用中文数字"二零二四年"、表格 chunk 正文无公司名，带约束 query 与文档表述不匹配时 reranker 分数被压低一半（实测 `二零二四年毛利率`=0.83 vs `2024年毛利率`=0.22）。**分数标尺实测：相关 chunk（0.14-0.37）与无关 chunk（0.07-0.15）区间重叠，绝对阈值无法区分**。
3. **`RERANK_MIN_SCORE=0.3` 绝对阈值误杀**：相关 chunk 大量落在 0.14-0.17，被 0.3 全拦 → abstain。交叉编码器分数是相对排序信号、无绝对意义，阈值应只做排序 + 由 LLM 做相关性判定。

此外 `grader_node` + 重试环是死逻辑（rewrite 纯函数 + 检索/评分确定性 → 重试必复现失败），且 `jieba` 覆盖度与 rerank 分数是两套矛盾信号。澄清链路存在**连环问**问题（实测 session 5 轮往返才问完公司/指标/期间，还有重复问）。

## What Changes

- **独立 LLM 改写（BREAKING）**：`rewrite_node` 改为 `make_rewrite_node(classify_llm)` 工厂（async），flash 单任务改写。medium → `standalone_query`；complex → `sub_queries`（2-4 条）。prompt 含约束保护（不篡改数字/公司/期间/否定）。
- **改写触发条件**：complex 必触发；medium 仅当"history 非空 或 len<10 或 含口语/省略词 或 含分析/解释/说明/为什么"触发，否则原样返回。**classify 已判定 query 完整时跳过**（避免批量澄清后组合消息被误触发）。
- **fallback 链**：LLM 失败 → 规则（`expand_query`/`condense_query`/`decompose_query`）→ 原 query。
- **多查询检索**：`retrieve_node` 并行遍历 `rewritten_queries`（改写结果 + 原 query 双路径）逐条 dense+BM25，N 路 RRF 合并去重。
- **rerank 去阈值（BREAKING）**：删除 `RERANK_MIN_SCORE` 绝对过滤，`rerank_results` 改为纯 top-N 相对截断。
- **abstention 判定（BREAKING）**：检索返回空（dense+bm25 无结果）→ 静态文案；检索非空 → top-N 全部交 LLM 语义判断（prompt 已支持 abstain 指令），LLM 决定能答或"未找到"。
- **删除 grader 与重试环（BREAKING）**：删 `grader_node`/`route_by_grader`/`RetrievalGrader`/重试短路；workflow `retrieve → rerank` 直连。
- **批量澄清（BREAKING）**：classify 列出**所有**缺失实体；`SSEClarificationEvent` 携带 `questions` 列表；前端多问题表单一次收集，组合成一条消息提交（1 轮往返替代连环问）。
- **abstention 引导**：检索不到时基于 KB 候选提示可查指标（如"东软 2025Q1 未披露毛利率，可查询：营收/净利润…"）。
- **状态清理**：删 `grader_score`/`retrieval_retries`/`downgraded`/`downgrade_reason`/`_prev_rewritten_query`；新增 `rewritten_queries: list[str]`。

## Capabilities

### New Capabilities
- `query-rewrite`: 独立 LLM 查询改写（触发条件、约束保护、fallback、sub_queries 分解）
- `multi-query-retrieval`: 多查询检索（双路径 + N 路 RRF 合并 + 去阈值相对 top-N）
- `batch-clarification`: 批量澄清（一次往返问完所有缺失维度）+ abstention 引导

### Modified Capabilities
- `retrieval-quality`: 删除 grader 与 `RERANK_MIN_SCORE` 绝对阈值，检索质量判定统一为"rerank 排序 + LLM abstain 判断"

## Impact

- **Graph**：`workflow.py` 删 grader 节点/边/`route_by_grader`（`retrieve→rerank` 直连）；rewrite 改工厂注册
- **State**：`state.py` 删 grader 字段与 `LangGraphNode.Grader` 类/常量；新增 `rewritten_queries`
- **Nodes**：`nodes.py` 删 `grader_node`；`rewrite_node` async 工厂；`retrieve_node` 多查询合并；`rerank_node` 去阈值
- **Service**：`agent_service.py` 删 Grader CHAIN_END 捕获；批量澄清发全部 questions；abstention 引导发 clarification
- **SSE**：`sse.py` `SSEClarificationEvent` question → questions 列表（保留单 question 兼容）
- **Frontend**：`chat.html` 澄清多问题表单 + 组合提交
- **Config**：`prompts.py` 新增 `REWRITE_SYSTEM_PROMPT`/`REWRITE_USER_TEMPLATE`；classify prompt 强化"列出所有缺失实体"
- **Retrieval**：`query_router.py` 新增 `_llm_rewrite`；`rag/retrieval.py` 删 `RERANK_MIN_SCORE` 过滤
- **删除**：`src/agents/grader.py`、`tests/agents/graph/test_grader.py`
- **评估**：`eval_ragas.py` 删 grader 字段；验收改为**难样本 abstain 率 + 幻觉率**（RAGAS 4 项因测试集字面匹配而虚高，不反映难样本）
- **测试**：删 grader 测试；新增 rewrite/多查询/批量澄清/去阈值测试；更新 `test_graph.py`/`test_agent_service.py` 断言
