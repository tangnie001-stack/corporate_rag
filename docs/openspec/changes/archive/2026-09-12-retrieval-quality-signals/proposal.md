# retrieval-quality-signals Proposal

## Why

检索质量无法在线定位。现状两条割裂的路径：

1. **离线**：RAGAS eval（`cli/eval_ragas.py` + `eval_report` 表）跑**通用测试集**算 4 指标——测的是机器生成的代表性问题，**碰不到用户真实问的刁钻问题**。
2. **在线**：agent 循环已用 prompt 软引导承担"检索不足 → 换词 → 转 web"判定（web-search-fallback 改造），但**没有任何结构化观测**，出了质量问题只能靠人工翻日志猜。

同时 bug3 记录了检索多样性缺陷：单文档重复入库占满 dense top-K → `_dedup_by_doc_id` 截到 1 条 → rerank 只有 1 个 context。

经参考 claude-code / deepseek-harness 论证：**在线检索质量判定不引入 LLM grader**（claude-code 验证靠真实执行 build/test，非 LLM judge；检索判定有硬证据——agent 直试能不能回答，故不该加前置相关性 LLM）。正确做法是**把 agent 已做出的行为投票采集为信号**，零额外 LLM 成本。

## What Changes

### 1. agent 行为信号（在线，零额外 LLM）

在既有代码路径（检索/联网/judge/拒答）采集 6 种行为信号，作为"这条 query 检索质量差"的在线判据：

| 信号 | 语义 | 埋点位置 |
|---|---|---|
| `reretrieve` | 同 turn 二次检索（换词投票） | rag_tools.retrieve_kb |
| `to_web` | 绑 KB 却转联网（检索不足投票） | verify_node / agent |
| `abstain_after_retrieve` | 检索过但最终拒答 | agent_service._is_abstention |
| `unsupported` | judge 打标无支撑句 | verify_node.faithfulness |
| `cited` | 正常引用（对照基线） | format_node |
| `empty_result` | 检索返回空（KB 态真缺陷） | rag_tools.retrieve_kb |

**态 A（纯对话）不产检索质量信号**：态 A 的 search_web 是主路径、retrieve_kb 空是设计行为——`to_web`/`empty_result` 等仅在 `kb_id` 非空时激活，避免把正常联网误标成缺陷。

信号经日志 change 的统一 helper（`retrieval_signal:` 前缀）输出，trace_id 自动注入。

### 2. dedup 多样性实验（3b）

`_dedup_by_doc_id`（retrieval.py:27-50）从"每文档 1 条"改为可配置"N 条/文档"，用**现有 RAGAS 指标**对"针对真实差 query 的小测试集"做 A/B 对照，确定最优 N。不引入新 grader。

## Capabilities

### New Capabilities
- （无 — 行为信号观测纳入日志 change 的 `observability-logging`，不另建能力）

### Modified Capabilities
- `retrieval-quality`: 新增"在线检索质量诊断"——agent 行为信号作为检索质量差的在线判据（替代已删除的 LLM grader 思路）；检索结果去重策略参数化支持多样性 A/B

## Impact

- `src/agents/tools/rag_tools.py` — retrieve_kb 埋 reretrieve / empty_result 信号（态 B 激活）
- `src/agents/graph/verify/`（Change 1 C1-5 拆包后的位置；含 faithfulness） — 埋 to_web / unsupported 信号；**注入联网指引时置 `ctx.web_guided=True`**（② 指派联网标记，供 search_web 排除 to_web 误报）
- `src/infra/llm/request_context.py` — 加 `web_guided: bool` 字段
- `src/agents/tools/web_tools.py` — search_web 读 `ctx.web_guided`，为 True 时不产 to_web 缺陷信号
- `src/services/agent_service.py` — abstain_after_retrieve 信号
- `src/agents/graph/nodes.py`（format_node） — cited 对照基线信号
- `src/rag/retrieval.py` — `_dedup_by_doc_id` 参数化（每文档 N 条，配置进 settings）
- `src/cli/eval_ragas.py` / `check_retrieval.py` — 支持吃"行为信号标记 query 集"做针对性 A/B
- **依赖**：① logging-convention-migration（helper + retrieval_signal 日志行）先行或同步；② agent-loop-hardening（C1-2 决策化后 verify 读 search_web queries，与 to_web 埋点同处 verify/search_web 交界）先行——**本 change 排在 agent-loop-hardening 之后**
- 测试：信号埋点单测（态 A 不产信号 / 态 B 激活 / web_guided 排除 ②）、dedup A/B 对照实验脚本
