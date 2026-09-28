## Why

主 agent 与 fork 子代理**跑的是两套循环实现**：主图自建 `StateGraph`（`src/agents/graph/workflow.py` 的 `agent ⇄ tools`），子代理走 `create_agent`（`src/agents/skills/executor.py:272`）。两套实现各自演化，任何循环层面的改进（上限语义、温度分档、观测字段）都要写两遍，且两边已经出现行为分叉（主 agent 触顶只记日志、子代理触顶有兜底文案）。

上游两家（claude-code `src/query.ts:276` 的单一 `query()`、deepseek-harness 的单一 `ctx.agents.create()`）都是**一套循环代码、两种角色**，差异全部由上下文与作用域提供。`create_agent` 在 LangChain 1.0 被明确定位为「构建 agent 的标准方式」，且官方 subagents 模式（`multi-agent/subagents`）与 Deep Agents 的产品化实现都是「主/子都用 `create_agent`」。

**为什么现在做**：`skill-execution-and-delegation`（最高优先级，执行中）已把子代理侧彻底 `create_agent` 化，并**接管了子代理的观测接线**（其 D11：同 trace、委派父 span、`scope=delegate`、显式喂事件、`set(None)` 保持不变）。子角色这一半已经被理顺，主图侧的「另一套循环」成了唯一的不一致来源。此外本变更的调研与探针已把全部机制风险前置验证（见 `docs/tmp/deep-research-one-loop-two-roles.md` 与本 change 的 design）。

## What Changes

- **L1（循环本体）统一到 `create_agent`**：新增装配工厂 `build_agent(model, tools, system 提供方式, name, middleware, 上限参数)`，**主/子角色共用**。主图的 `agent` 节点改为「节点内组装首轮消息 + invoke 该子图 + 回写」；`create_agent` 的内建条件边取代 `route_agent`。
- **L2（外壳）参数化到 middleware**：system 段的施加、KB 温度分档、`extra_body.enable_thinking`、回合上限、Langfuse `agent_turn` generation span 全部由 middleware 承担。
- **L3（领域阶段）不动**：`verify` / `format` / `agent_finalize` / `skill_direct` 仍是外层图节点，位置与职责不变（`skill_direct` 依据 `skill-execution-and-delegation` 的 D4 保留）。
- **首轮组装一次完成、按类型拆两半**：`build_prompt(...)` 的产出里，system 段写入**外层 `AgentState` 的新增声明字段**（跨 invoke 持久，含未绑 KB 时的第二条 system），非 system 段（注入 + 历史 + 当前 user）作为子图输入 ⇒ `build_system_prompt` 每次生成仍只调一次，`prompt assembled` / `prompt messages` 两条日志的**产点与字段零变化**；**重生成轮**照常取到同一份 system 段。
- **工具取图状态的形状改为与图实现无关**：本仓 5 处工具取数（`rag_tools.py:102/187`、`ask_tools.py:82/83/166/167`）现用属性访问，而 `create_agent` 经 `InjectedState` 注入的是 **`dict`** ⇒ 会 `AttributeError` 并被错误回喂吞成工具错误（**检索恒空、澄清恒失败**，实测复现）。改为**显式形状判定**（`isinstance` 分支，不用 `getattr` 兜底）并补断言。
- **回合上限改用自定义 middleware**（不用官方 `ModelCallLimitMiddleware`——它会注入一条英文限流 AIMessage）：判定放 `after_model`，命中时 `jump_to: end`，**不注入任何消息**；`iteration limit` 日志**与 jump 判定解耦**（计数达上限即记，与"该轮是否仍声明工具调用"无关，与今天一致）；委派放宽取「**本轮命中先置位** 或 **此前已置位**」两者兼有。
- **`AgentState` 删除三个循环字段**（`_agent_iterations` / `_max_agent_iterations` / `_delegate_used`）并删除 `route_agent` 函数；**BREAKING（内部契约）**——`const.py` 的 `MAX_AGENT_ITERATIONS` / `MAX_DELEGATE_BONUS` 仍保留为 middleware 参数。
- **SSE 转换层判别谓词小改**：主循环的模型事件 `metadata.langgraph_node` 由 `"agent"` 变为 `"model"`，判据只用「节点判别键 + `scope == "main"`」两维（**不引入 `checkpoint_ns` 兜底**）；域节点（`format` / `agent_finalize` / `skill_direct`）与 `ToolTraceCollector` 的 `"tools"` 判据**均不变**；同模块 docstring 里残留的 `"agent"` 一并更正。
- **删除 regen 轮的预算复位管道**（`guardrails.py:105/106`、`:180/181`、`regen_decision.py:174/175` **三处**的 `_agent_iterations` 与 `_delegate_used`）：每次 `invoke` 都是新 run，计数与委派标志天然从初值起（已实测）。`ctx.web_count = 0`（**两处**：`guardrails.py:99`、`regen_decision.py:169`）与主循环预算无关，**保留**。
- **明确不做**：不改 `verify` / `format` 的判据与日志事件；不改工具面（归 `skill-execution-and-delegation` D7/D8）；不改轮次/时长护栏**取值**（归 `agent-round-budget`）；不新增步数硬兜底（今天本就没有，已实测）；不动 `skills/` 内容。

## Capabilities

### New Capabilities

- `agent-assembly`：**唯一的 agent 装配入口**——主 agent 循环与 fork 子代理 SHALL 由同一装配入口生成，差异仅由参数（system 提供方式 / 工具面 / 回合上限 / middleware 集合 / 角色名）提供；含五条硬约束：装配入口不产出编译日志、middleware 不得持有 per-request 实例状态、**工具取图状态 MUST 与图实现无关**（`InjectedState` 在两种承载下形状不同）、回合上限参数化、委派放宽须「本轮先置位 OR 此前已置位」。

### Modified Capabilities

- `delegate-progress-observability`：主图事件判别键由 `"agent"` 改为 `"model"`（该 spec 现明写 `metadata.langgraph_node == "agent"`）。
- `answer-verification`：把「`_agent_iterations` 上限只管主循环」改为行为化表述（字段已迁入 middleware，行为不变）。
- `delegate-task`：该 spec 现写着「`create_agent` 的 middleware 参数保留装配位但默认传空（v1 不启用）」——本变更启用按角色装配的 middleware。⚠️ 该 requirement 同时被 `skill-execution-and-delegation` 修改，归档顺序须在其后并 rebase（见该 delta 文件顶部提示）。
- `prompt-composition`（ADDED）：补明「system 段送入模型请求时逐字不变」这一此前未规定的契约，含未绑 KB 时的**第二条** system 消息。
- `agent-loop-observability`（ADDED）：把触顶路径的**确切消息语义**钉住（模型调用 = 上限次、工具执行 = 上限−1 次、末条为含 `tool_calls` 的 `AIMessage`、`answer` 可为空串）——这是既有缺陷的载体，必须防止被无意改坏。

## Impact

**代码**

- 新增 `src/agents/graph/agent_factory.py`（`build_agent` 装配工厂）与 `src/agents/graph/middleware.py`（system 施加 / 回合预算 / Langfuse span 三件套）——`agent_node.py` 现 391 行，加 middleware 必超 400 行红线
- `src/agents/graph/agent_node.py` — 删 `make_agent_model_node` / `make_agent_tools_node` / `route_agent`；保留并裁剪 `_initial_messages`（产两半）与 `make_agent_finalize_node`
- `src/agents/graph/workflow.py` — `agent` 节点改为 invoke 子图的包装节点；`tool_sink` / `delegate_task` / `skill_direct_node` 注入面不变
- `src/agents/graph/state.py` — 删三个循环字段，**新增一个承载 system 段的声明字段**（跨 invoke 持久）；`timings` / `_token_usage` 是已核实死字段，不在本变更处理，仅登记
- `src/agents/tools/rag_tools.py` / `src/agents/tools/ask_tools.py` — 5 处工具取数改**显式形状判定**（`InjectedState` 在 `create_agent` 下是 `dict`）
- `src/services/agent_service.py` — `_convert_event` 的模型事件判别谓词 + 同模块 docstring 里残留的 `"agent"`
- `src/agents/graph/verify/guardrails.py` 与 `src/agents/graph/verify/regen_decision.py` — 删两文件共三处 regen 预算复位（`_agent_iterations` / `_delegate_used`）；`ctx.web_count` 保留
- `src/agents/skills/executor.py` — `_build_sub_agent` 改调 `build_agent`（**依赖 `skill-execution-and-delegation` 的 P1+P2 先落地，为开工闸门**）

**测试**：12 个文件受影响，见 design 的 Impact 与 tasks 的测试组

**文档**：`code-map.md` / `data-flow.md` / `api_contract.md` / `glossary.md` / `prompt-ownership.md` / `defensive-patterns.md`（新增缺陷类别：middleware `state_schema` 静默丢弃未声明键）

**依赖与排序**：`skill-execution-and-delegation`（最高优先级）在先——其 P1 已将子代理侧 `create_agent` 化并提供 `ToolTraceCollector` 的参数化接口，其 P2 会改写 `executor.py`。本变更 **apply 排在其后**；`agent-round-budget` 排在本变更之后（其标定对象是统一后的循环）。

**ADR**：本变更含不可逆取舍（循环统一到 `create_agent`、领域阶段留外层图、回合上限改用 middleware），需按 `docs/adr/README.md` 撰写；**编号须在 `skill-execution-and-delegation` 写完之后取**（现最大 `0015`，其将先占 `0016`）。
