# agent-loop-hardening Proposal

## Why

P0 建立了 agent 循环骨架（kb_router → agent → verify → format），但存在四类问题阻碍"单 Agent 可靠"：

1. **regen 与迭代计数耦合（手测发现 bug）**：`verify` 触发重生成时，`_agent_iterations` 同时被主循环上限和 verify 修订轮数复用。首轮 agent 耗尽多次迭代后，verify 注入 SystemMessage 置 regen → 回 agent 已近/达上限 → `route_agent` 强制收尾，search_web 工具调用被吞，**AI 答案空白**。
2. **kb_router 空壳节点**：跨库语义废弃后 `kb_router_node` 只是穿透（kb_id → [_resolved_kb_ids]），`_resolved_kb_ids` 是 `[kb_id]` 的冗余镜像，图拓扑名实不符（且 `src/rag/kb_router.py` 的 KBRouter 类已成无调用者死代码）。
3. **KB 答案无溯源**：绑定 KB 且调了 retrieve_kb 拿到 context，但模型答案不带 `[n]` 时，format_node 提取不到引用 → 前端来源横条消失（KB 场景的 bug1 变体，P0 只修了无 KB 联网场景）。
4. **verify_node.py 逼近 400 行红线**（323 行 + 上述叠加必超），需结构重组。

## What Changes

### C1-5 verify 包化 + 校验器管道（地基，先做）
`verify_node.py` 拆为 `verify/` 包，**两态两管道**结构（关键修正：纯对话与 KB 不是同一套校验器）：
- 态 A（未绑 KB，纯对话）: pipeline = `[web_citation_guard]`
- 态 B（绑 KB）: pipeline = `[completeness, kb_citation_guardrail, faithfulness]`
- 共享底层：regen 预算 `_verify_regenerations`（C1-2）、SystemMessage 注入去重

### C1-1 kb_router 下移
删图节点 + `LangGraphNode.KbRouter`，entry 直连 agent，`_resolved_kb_ids` → `state.kb_id`（消费点：rag_tools/verify/ask_tools 全换源）；清 KBRouter 死代码与 build_graph 死参数。

### C1-2 verify regen × 主循环迭代解耦（verify 决策化）
state 加 `_verify_regenerations`，const 加 `MAX_VERIFY_REGENERATIONS=2`（兜底保险丝，正常被决策提前终止）；verify 三处 `_agent_iterations` 检查换源。

**核心：verify 从"盲目重试 N 次"升级为"依据 agent 上一轮实际动作决策是否值得重试"**（取代 already_guided 补丁）：
- 依据 search_web 上一轮调用的 `queries` 覆盖度判断：
  - 没调过 search_web → 值得试 → 注入指引 regen
  - 调了但 queries **没带全** missing 年份 → agent 没执行好 → regen（指引强调一次带全，search_web 本就支持并行多 query）
  - 调了且 queries **带全** 但仍缺 → 网上真没有 → **停，标注缺失直通**（不再试）
- 态 A web 引导 regen 与态 B 完整性/护栏 regen 共享预算

### C1-3 输出护栏（KB 强制溯源）
态 B 加校验器：`has_kb_context ∧ 无[n] ∧ 非拒答 ∧ 非"知识库未覆盖"` → 注入 KB citation marker SystemMessage → regen。

## Capabilities

### New Capabilities
- （无 — 均为既有能力升级）

### Modified Capabilities
- `answer-verification`: 新增 KB 答案强制溯源护栏；regen 终止条件改为独立 `_verify_regenerations` 计数；verify 结构改为两态校验器管道
- `kb-routing`: 语义路由节点移除，KB 解析下沉 retrieve_kb 工具内部（`_resolved_kb_ids` 退役，改读 `state.kb_id`）；跨库语义路由正式删除

## Impact

- `src/agents/graph/workflow.py` — 删 kb_router 节点，entry 直连 agent（图最后动一次）
- `src/agents/graph/nodes.py` — 删 `make_kb_router_node` / `LangGraphNode.KbRouter`
- `src/agents/graph/state.py` — 删 `_resolved_kb_ids`，加 `_verify_regenerations`
- `src/agents/graph/verify_node.py` → `src/agents/graph/verify/` 包（node/checks/guardrails/ask_confirm/faithfulness），导入名不变
- `src/config/const.py` — 加 `MAX_VERIFY_REGENERATIONS`、KB citation marker
- `src/agents/tools/rag_tools.py` / `ask_tools.py` — kb 消费点换源
- `src/rag/kb_router.py` — 删除（KBRouter 死代码）；`workflow.py`/`make_rag_tools` 死参数清理
- 测试：`test_graph.py`（拓扑断言去 kb_router）、`test_verify_node.py`、`test_rag_tools.py` + 新增 regen 边界图集成测试
