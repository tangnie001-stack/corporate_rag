## Why

本项目目前只有**一层最朴素的上下文处理**，且有两处实质错误：

1. `_truncate_history`（`src/agents/graph/agent_node.py:28-57`）的 `context_window` 默认**写死 8000**，而调用点 `:119` 不传参 ⇒ 实际预算 = 0.3×8000 = **2400 token**；配置 `MODEL_CONTEXT_WINDOW_TOKENS=32768` 从未参与。预算口径是「窗口 × 比例」，比例会随窗口漂移，且写死默认值与配置**自相矛盾**。
2. token 计数是 `len(content) // 2` 粗估，**存在两份**（`agent_node.py:53,56` 内联、`src/infra/llm/token_usage.py:20-21`）。

此外轮内累积（工具结果/检索块/思考回传）**无度量**、无任何上限；实测同轮输入可达 6.17 万 token。

**范围说明（经独立架构评审后收窄）**：初版曾包含 per-model 窗口注册表、离线探针 CLI、工具调用中间件治理。评审指出其中两处 Blocker——① 工具钩子在结果产生瞬间调用，此时结果**恒为「当前轮」**，而「该截断的集合」恰好等于「被保护的集合」，L0 截断按字面实现是**空操作**；② 「阈值 = 输入上限 − reserve」与「默认预算显著小于上限」**自相矛盾**，且「上限」数值在 Phase A 中并不影响结果。故本变更收窄为**地基三件**：预算口径收口、分词器计数、轮内度量。窗口注册表/探针/治理中间件**移至 Phase B**（摘要阈值才真正需要窗口数值）。

## What Changes

- 新增**单一保守绝对历史预算**（集中 `src/config/const.py`），取代「`HISTORY_TOKEN_RATIO` × 写死 8000」；`_truncate_history` 按该绝对预算细裁，删除写死默认值；`HISTORY_MAX_TURNS` 保留为**轮数粗筛**。
- **token 计数两处**改用已装依赖 `tiktoken`（`estimate_usage` 与 `_truncate_history` 内联）。
- 新增**轮内累积度量与可观测信号**：在既有模型调用钩子上度量单轮输入 token，超阈值记日志；**不新增工具钩子、不做截断**。
- 同步修正受影响的既有测试（两处硬编码了口径）。
- `MODEL_CONTEXT_WINDOW_TOKENS` 保留（供 `src/rag/prompt.py` 段落占比告警），并在注释中标注其**仍为假设值**。

## Capabilities

### New Capabilities
- `context-budget`: 历史预算的唯一口径（集中的绝对 token 量、轮数粗筛）与轮内累积的度量/可观测信号。

### Modified Capabilities
- `token-usage-model`: token 计数从 `len(content)//2` 粗估改为分词器（tiktoken）计数；`TokenUsage` 形状与 `total_tokens` 不变量不变。

## 明确不做（移出本变更）

- **per-model 窗口注册表 / 解析器 / 离线探针 CLI** → Phase B（摘要阈值需要窗口数值时才引入；当前预算为固定保守值，与窗口无关）。
- **工具结果截断/去重/占位 与工具调用中间件** → Phase B/D（写入侧无法判定「被引用」，且本项目唯一可能超长的工具结果恰在当前轮）。
- LLM 摘要压缩、LangGraph checkpointer 短期记忆统一、长期记忆 Store。
- 「触顶 → 空回答」缺陷（归属需求池 F-37）。

## Impact

- **代码**：`src/config/const.py`（新增绝对预算常量、废弃 `HISTORY_TOKEN_RATIO`）、`src/agents/graph/agent_node.py`（`_truncate_history` 取数与内联计数）、`src/infra/llm/token_usage.py`（`estimate_usage` 计数）、`src/agents/graph/middleware.py`（复用既有模型调用钩子加轮内度量，**不新增钩子类型**）。
- **依赖**：无新增第三方依赖（`tiktoken==0.13.0` 已在环境）。
- **行为**：历史预算从实际 2400 token 变为配置的保守绝对值；**CJK 文本的计数会显著变化**（实测 `len//2` 与 tiktoken 在中文语料上相差约 **2.57×**），保留条数随之变化——属预期行为变化，非 API 破坏。
- **测试**：`tests/agents/graph/test_history_window.py:17,30,44`（显式传 `token_ratio`/`context_window`）与 `tests/agents/graph/test_loop_middleware.py:550`（断言 `usage["input"] == 9`，依赖 `len//2`）**必然受影响**，需按新口径更新断言；新增预算与计数的单元测试。
- **文档**：`docs/adr/0017-context-ladder-and-memory-scope.md`（方向决策，已存在；本变更只落地其地基部分）；`docs/context-memory-research.md`（调研大图，已存在）。
