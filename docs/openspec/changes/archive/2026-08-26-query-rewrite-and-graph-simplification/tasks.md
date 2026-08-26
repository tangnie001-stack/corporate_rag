## 1. 删除 grader 与检索重试环

- [ ] 1.1 `workflow.py` 删除 `grader_node` 注册、`retrieve→grader` 边、`route_by_grader` 条件边，改为 `retrieve→rerank` 直连
- [ ] 1.2 `nodes.py` 删除 `grader_node` 函数及 `RetrievalGrader`/`DOWNGRADE_REASON_REWRITE_NO_INCREMENT` import
- [ ] 1.3 `agent_service.py` 删除 Grader 的 CHAIN_END 捕获分支，`downgraded`/`downgrade_reason` 日志参数改常量
- [ ] 1.4 `state.py` 删除 `LangGraphNode.Grader` 类、`DOWNGRADE_REASON_REWRITE_NO_INCREMENT` 常量及 `grader_score`/`retrieval_retries`/`downgraded`/`downgrade_reason`/`_prev_rewritten_query` 字段
- [ ] 1.5 `eval_ragas.py` 初始 state 删除 `retrieval_retries`/`downgraded`/`downgrade_reason` 三行
- [ ] 1.6 删除 `src/agents/grader.py` 与 `tests/agents/graph/test_grader.py`
- [ ] 1.7 `test_graph.py` 删除 3 个 grader_node 测试，`test_state.py` 删除 downgrade 字段测试
- [ ] 1.8 全仓 grep 确认无 `grader`/`retrieval_retries`/`downgraded`/`_prev_rewritten_query` 残留引用

## 2. rerank 去阈值与 abstention 改造

- [ ] 2.1 `rag/retrieval.py` `rerank_results` 删除 `RERANK_MIN_SCORE` 绝对过滤，改为纯 top-N 相对截断（保留 fallback 原始顺序逻辑）
- [ ] 2.2 `nodes.py` `rerank_node` 打分策略：medium 用 `state.query`（原始 query），complex 逐子查询打分（每条子查询对合并候选池打分取 top 合并）
- [ ] 2.3 `nodes.py` `generate_node` abstention 判定：检索空（`retrieval_results` 为空）→ 静态文案；检索非空但 contexts 非空 → 交给 LLM 语义判断（prompt 已有 abstain 指令）
- [ ] 2.4 更新 `rerank_results`/`generate_node` 相关测试（移除 0.3 阈值断言，新增相对 top-N 与 LLM 判定断言）

## 3. 独立 LLM 改写

- [ ] 3.1 `config/prompts.py` 新增 `REWRITE_SYSTEM_PROMPT`（单任务改写 + 约束保护 + 口语前缀精简）与 `REWRITE_USER_TEMPLATE`（query/route/history，JSON 含 standalone_query/sub_queries）
- [ ] 3.2 `infra/search/query_router.py` 新增 `_llm_rewrite(query, history, route)`：flash + JSON 解析（复用 token 统计）+ 失败回退规则（`expand_query`/`condense_query`/`decompose_query`）→ 原 query
- [ ] 3.3 `nodes.py` `rewrite_node` → `make_rewrite_node(classify_llm)` 工厂（async）：触发条件（complex 必触发；medium 仅 history 非空或 len<10 或口语词或分析词；**classify 判定 query 完整时跳过**），输出 `rewritten_queries`（改写结果 + 原 query 去重，原 query 必须保留）
- [ ] 3.4 `workflow.py` rewrite 节点改用工厂函数注册
- [ ] 3.5 `state.py` 新增 `rewritten_queries: list[str]` 字段，`rewritten_query` 保持（medium=standalone_query，complex=join）

## 4. 多查询检索

- [ ] 4.1 `bm25_index.py` 或 `rag/retrieval.py` 新增 generalized N 路 RRF 合并函数（现有 `rrf_fusion` 仅 2 源）
- [ ] 4.2 `nodes.py` `retrieve_node` 用 `asyncio.gather` 并行遍历 `rewritten_queries` 逐条 dense+BM25 检索，N 路 RRF 合并去重，空列表回退原 query；**RRF 合并后截断候选池到安全上限（确认 DashScope rerank documents 上限后定值，默认 100）**

## 5. 批量澄清与 abstention 引导

- [ ] 5.1 `prompts.py` classify prompt 强化："列出**所有**关键缺失实体（信息增益排序），不遗漏"
- [ ] 5.2 `sse.py` `SSEClarificationEvent` 结构扩展：`question` 单字段 → `questions: [{type, question, suggestions}]`（保留单 question 兼容序列化）
- [ ] 5.3 `agent_service.py` 澄清处理：遍历全部 `missing_entities` 构造 questions 列表（每维度取 KB 候选/`SUGGESTIONS_MAP` suggestions）
- [ ] 5.4 `agent_service.py` abstention 引导：检索空返回 abstain 且 KB 有候选时，发一次引导 clarification（提示可查指标）
- [ ] 5.5 `chat.html` `renderClarification` 改为多问题表单（每维度 chips + "其他"输入框 + 提交按钮），`submitClarification` 组合所有答案成一条消息提交
- [ ] 5.6 更新 `tests/services/test_agent_service.py` 澄清断言（questions 列表 + 组合消息 + abstention 引导）

## 6. 测试与验收

- [ ] 6.1 新增 `tests/rag/test_rewrite.py`：`_llm_rewrite` 成功/非法 JSON/异常回退、触发条件（含 classify 完整跳过）、约束保护断言
- [ ] 6.2 更新 `tests/agents/graph/test_graph.py`：rewrite 多查询输出、retrieve 并行合并、rerank 去阈值相对 top-N、complex 逐子查询打分
- [ ] 6.3 全量 `pytest tests/ -v` + `ruff check .` + `pyright src/`（不新增 error）
- [ ] 6.4 **难样本回归**（替代 RAGAS 虚高指标）：用 s1-s6 + session 真实轨迹构造难样本集，验收 **abstain 率下降 + 幻觉率 ≤ 阈值 + 目标 chunk 命中**
  - s2/s5（腾讯有数据）由 abstain 转命中；complex 子查询命中；session 场景 1 轮往返问完
- [ ] 6.5 **LLM abstain 可靠性基线实验（实现前置）**：s1-s6 + 注入无关 context 跑 generate，统计幻觉率；确认 qwen3.7-max 可靠 abstain，必要时强化 prompt（few-shot abstain 示例）
- [ ] 6.6 前端用 playwright-cli 验证澄清多问题表单交互（多选 + 组合提交 + "其他"输入）
