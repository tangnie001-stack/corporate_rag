## Why

主 agent 与 fork 子代理**跑的是两套循环实现**：主图自建 `StateGraph`（`src/agents/graph/workflow.py` 的 `agent ⇄ tools`），子代理走 `create_agent`（`src/agents/skills/executor.py:272`）。两套实现各自演化，任何循环层面的改进（上限语义、温度分档、观测字段）都要写两遍，且两边已经出现行为分叉（主 agent 触顶只记日志、子代理触顶有兜底文案）。

上游两家（claude-code `src/query.ts:276` 的单一 `query()`、deepseek-harness 的单一 `ctx.agents.create()`）都是**一套循环代码、两种角色**，差异全部由上下文与作用域提供。`create_agent` 在 LangChain 1.0 被明确定位为「构建 agent 的标准方式」，且官方 subagents 模式（`multi-agent/subagents`）与 Deep Agents 的产品化实现都是「主/子都用 `create_agent`」。

**为什么现在做**：`skill-execution-and-delegation`（最高优先级，**已于 2026-09-28 落地并归档**，归档于 `docs/openspec/changes/archive/2026-09-28-skill-execution-and-delegation/`）已把子代理侧彻底 `create_agent` 化，并**接管了子代理的观测接线**（其 D11：同 trace、委派父 span、`scope=delegate`、显式喂事件、`set(None)` 保持不变）。子角色这一半已经被理顺，主图侧的「另一套循环」成了唯一的不一致来源。此外本变更的调研与探针已把全部机制风险前置验证（见 `docs/tmp/deep-research-one-loop-two-roles.md` 与本 change 的 design）。

## What Changes

- **L1（循环本体）统一到 `create_agent`**：新增装配工厂 `build_agent(model, tools, system 提供方式, name, middleware, 上限参数)`，**主/子角色共用**。主图的 `agent` 节点改为「节点内组装首轮消息 + invoke 该子图 + 回写」；`create_agent` 的内建条件边取代 `route_agent`。
- **L2（外壳）参数化到 middleware**：system 段的施加、KB 温度分档、`extra_body.enable_thinking`、回合上限、Langfuse `agent_turn` generation span 全部由 middleware 承担。**仅主角色装配**（子角色 middleware 集合为空——其模型轮次记录由 fork 委派的事件消费侧承担，图内不重复产出）。模型参数的两个取值（档位判据、思考开关）**由装配从外层状态带入子图**；middleware 读图状态一律用 `state.get(...)`（实测拿到的是映射，属性访问会抛 `AttributeError`）。
- **L3（领域阶段）不动**：`verify` / `format` / `agent_finalize` / `skill_direct` 仍是外层图节点，位置与职责不变（`skill_direct` 依据 `skill-execution-and-delegation` 的 D4 保留）。
- **首轮组装一次完成、按类型拆两半**：`build_prompt(...)` 的产出里，system 段写入**外层 `AgentState` 的新增声明字段**（跨 invoke 持久，含未绑 KB 时的第二条 system），非 system 段（注入 + 历史 + 当前 user）作为子图输入 ⇒ `build_system_prompt` 每次生成仍只调一次，`prompt assembled` / `prompt messages` 两条日志的**产点与字段零变化**；**重生成轮**照常取到同一份 system 段。
- **装配后图内工具与中间件仍能取到必需的运行态字段**：`create_agent` 经 `InjectedState` 注入的是 **映射**（外层自建图给 `AgentState` 实例），属性访问会 `AttributeError` 并被错误回喂吞成工具错误（**检索恒空、澄清恒失败**）。`retrieve_kb` 的 `kb_id` 守卫**已由 `skill-execution-and-delegation` 落地时修复**（`8014d64`）；本变更补两处：① `ask_tools.py` 的 **3 处**取数（对方有意未修——`ask_user` 在 fork 禁用集里、其范围内不是活 bug；但主循环改由 `create_agent` 承载后就是活 bug）；② `retrieve_kb` 的**迭代序号**（其现有守卫在映射下**恒取 0**，会丢主循环真实轮次）。**必需字段一律由装配从外层 `AgentState` 带入子图**（至少 `kb_id` / `query` / 迭代序号 / `deep_thinking`）——**未传入的键在子图状态里根本不存在**（实测：未 seed 时只有 `messages` 一个键，schema 默认值不会被填），故不能把 `RequestContext` 当"总是有"：`kb_id` 与 `deep_thinking` 在 ctx 里虽有对应项，但 **CLI 评估入口不建 ctx**（`kb_id` 只放进图输入）⇒ 回退落空。工具侧保留「注入优先 + `ctx` 回退」；字段缺失降级 SHALL 记 warning（不得静默取空）。
- **回合上限改用自定义 middleware**（不用官方 `ModelCallLimitMiddleware`——它会注入一条英文限流 AIMessage）：判定放 `after_model`，命中时 `jump_to: end`，**不注入任何消息**；`iteration limit` 日志**与 jump 判定解耦**（计数达上限即记，与"该轮是否仍声明工具调用"无关，与今天一致）；委派放宽取「**本轮命中先置位** 或 **此前已置位**」两者兼有。**本条只适用于主角色**——子角色的轮次上限仍由 fork 委派的事件消费侧判定，本变更不改该机制。
- **外层图结构随之收缩**：删除外层 `tools` 节点与 `tools → agent` 回边（循环已内化进装配产物），`agent` 的出边由条件边改为**直连 `agent_finalize`**；`ToolTraceCollector` 的 `langgraph_node == "tools"` 判据**继续有效**（工具事件来自子图内同名节点）。
- **`AgentState` 删除三个循环字段**（`_agent_iterations` / `_max_agent_iterations` / `_delegate_used`）并删除 `route_agent` 函数；**同时新增一个声明字段承载 system 段**（跨 invoke 持久，重生成轮据此取到同一份 system）；**BREAKING（内部契约）**——`const.py` 的 `MAX_AGENT_ITERATIONS` / `MAX_DELEGATE_BONUS` 仍保留（后者成为主角色预算 middleware 的参数）。
- **SSE 转换层判别谓词小改**：主循环的模型事件 `metadata.langgraph_node` 由 `"agent"` 变为 `"model"`（**不引入 `checkpoint_ns` 兜底**——该值含 uuid、只承载外层节点名，属更弱证据）；域节点（`format` / `agent_finalize` / `skill_direct`）与 `ToolTraceCollector` 的 `"tools"` 判据**均不变**；同模块 docstring 里残留的 `"agent"` 一并更正。⚠️ 生效的判据实际只有**节点判别键一维**——转换器的 `scope` 形参存在但**所有调用点都用缺省 `main`**（其 docstring 自陈当前实现只在 `scope=="main"` 时转换）；主 SSE 与子代理事件的隔离由**事件路由**承担（切断回调继承 + 委派事件显式喂给委派域）。改谓词后，子代理的模型事件（节点名同为 `"model"`）**不再被偶然挡住** ⇒ 须补一条「fork 事件不泄漏进主 SSE」的回归断言守护该单点机制。
- **删除 regen 轮的预算复位管道**（`guardrails.py:105/106`、`:180/181`、`regen_decision.py:174/175` **三处**的 `_agent_iterations` 与 `_delegate_used`）：每次 `invoke` 都是新 run，计数与委派标志天然从初值起（已实测）。`ctx.web_count = 0`（**两处**：`guardrails.py:99`、`regen_decision.py:169`）与主循环预算无关，**保留**。
- **明确不做**：不改 `verify` / `format` 的判据与日志事件；不改工具面（归 `skill-execution-and-delegation` D7/D8）；不改轮次/时长护栏**取值**（归 `agent-round-budget`）；不新增步数硬兜底（今天本就没有，已实测）；不动 `skills/` 内容；不改子角色的轮次上限机制（仍在 fork 消费侧）；**不清理四个已核实死字段**（`timings` / `_token_usage` / `model_used` / `is_fallback`，仅登记）；不触碰 `web_tools` / `tavily_client` / `agent_service` 三组临时取证埋点（仅登记待办）。

## Capabilities

### New Capabilities

- `agent-assembly`：**唯一的 agent 装配入口**——主 agent 循环、`delegate_task` 委派子代理与 `/xxx` 直出子代理 SHALL 由同一装配入口生成，差异仅由参数（system 提供方式 / 工具面 / 回合上限 / middleware 集合）提供，且两角色共用同一图状态 schema。六条 requirement 含四条硬约束：装配入口不产出编译日志、middleware 不得持有 per-request 实例状态、**装配后图内工具与中间件仍能取到必需的运行态字段**（`InjectedState` 在两种承载下形状不同；`kb_id` 走 `ctx` 回退、`query` 与迭代序号须由装配带入图状态、缺字段降级须记 warning）、**回合上限参数化且委派放宽须「本轮先置位 OR 此前已置位」**（该条**只适用于主角色**——子角色上限仍在 fork 消费侧）。

### Modified Capabilities

- `delegate-progress-observability`：主图事件判别键由 `"agent"` 改为 `"model"`（该 spec 现明写 `metadata.langgraph_node == "agent"`）。
- `answer-verification`：把「`_agent_iterations` 上限只管主循环」改为行为化表述（字段已迁入 middleware，行为不变）。
- `delegate-task`：该 spec 现写着「`create_agent` 的 middleware 参数保留装配位但默认传空（v1 不启用）」——本变更改为按角色装配（主角色四件套、子角色为空）。该 requirement 同时被 `skill-execution-and-delegation` 修改 ⇒ 本 delta **已 rebase 到其落地后的主规格文本**（2026-09-28）；归档时若该 requirement 又有新改动，须重做一次 rebase（见该 delta 文件顶部说明）。
- `prompt-composition`（ADDED）：补明「system 段送入模型请求时逐字不变」这一此前未规定的契约，含未绑 KB 时的**第二条** system 消息；并补「组装事实日志的消息构成计数逐字不变」。
- `agent-loop-observability`（ADDED）：把触顶路径的**确切消息语义**钉住（模型调用 = 上限次、工具执行 = 上限−1 次、末条为含 `tool_calls` 的 `AIMessage`、`answer` 可为空串）——这是既有缺陷的载体，必须防止被无意改坏；并补「循环域三条日志的字段值逐字保持」。

## Impact

**代码**

- 新增 `src/agents/graph/agent_factory.py`（`build_agent` 装配工厂）与 `src/agents/graph/middleware.py`（system 施加 / 模型参数 / 回合预算 / Langfuse span **四件套**——新增的**模型参数 middleware** 承接今天内联在调用点的温度分档与 `extra_body.enable_thinking`，其档位判据取**请求上下文**的绑定状态而非图状态）——`agent_node.py` 现 391 行，加 middleware 必超 400 行红线
- `src/agents/graph/agent_node.py` — 删 `make_agent_model_node` / `make_agent_tools_node` / `route_agent`；保留并裁剪 `_initial_messages`（产两半，**消息构成计数须在拆分前算出**）与 `make_agent_finalize_node`；随迁移**删除** TTFB 临时取证埋点与 `[agent] TIMING` 日志（`completion_start_time` 属功能字段，保留）
- `src/agents/graph/workflow.py` — `agent` 节点改为 invoke 子图的包装节点；**删除外层 `tools` 节点与 `tools → agent` 回边**，`agent` 出边改为直连 `agent_finalize`；`tool_sink` / `delegate_task` / `skill_direct_node` 注入面不变
- `src/agents/graph/state.py` — 删三个循环字段，**新增一个承载 system 段的声明字段**（跨 invoke 持久）；已核实死字段是**四个**（`timings` / `_token_usage` / `model_used` / `is_fallback`），不在本变更处理，仅登记
- `src/agents/tools/ask_tools.py` — 3 处取数（`:84` / `:85` / `:168-169`）改显式形状判定（`kb_id` 按既有口径走 `ctx` 回退）
- `src/agents/tools/rag_tools.py` — 仅补**迭代序号**一处（`kb_id` 与形状守卫已由 `skill-execution-and-delegation` 落地）
- `src/services/agent_service.py` — `_convert_event` 的模型事件判别谓词 + 同模块 docstring 里残留的 `"agent"`
- `src/agents/graph/verify/guardrails.py` 与 `src/agents/graph/verify/regen_decision.py` — 删两文件共三处 regen 预算复位（`_agent_iterations` / `_delegate_used`）；`ctx.web_count` 保留
- `src/agents/skills/executor.py` — `_build_sub_agent` 改调 `build_agent`（**前置已满足**：`skill-execution-and-delegation` 的 P1+P2 已落地并归档）

**测试**：测试面 **12 个**文件受影响（清单见 tasks §7）；其中 **5 个文件的 `langgraph_node: "agent"` 事件夹具与 1 份谓词副本须同步核对**（否则它们会继续绿、却已与现实脱节）——逐文件处置见 tasks 3.5。

**文档**：**6 份**（`code-map.md` / `data-flow.md` / `api_contract.md` / `glossary.md` / `prompt-ownership.md` / `defensive-patterns.md`）——逐项见 tasks §8。其中 `defensive-patterns.md` 在本变更立项时已登记两条 middleware 规则，实现期再复核 `extra_body` 浅合并配置陷阱是否需补登。

**依赖与排序**：`skill-execution-and-delegation` **已于 2026-09-28 落地并归档**（归档 `c6c8ea8`、合并 `95c81ff`）——子代理侧已 `create_agent` 化、`ToolTraceCollector` 已参数化（缺省行为不变）、其工具面与委派预算已在库 ⇒ **本变更的前置闸门已满足，随时可开工**。`agent-round-budget` 排在本变更之后（其标定对象是统一后的循环）。

**ADR**：本变更含不可逆取舍（循环统一到 `create_agent`、领域阶段留外层图、回合上限改用 middleware），需按 `docs/adr/README.md` 撰写；编号按 **`dev-wsl` 当时的最大号 +1** 取（当前最大 `0015`，故为 `0016`），取号前须与当时的在途变更协调。
