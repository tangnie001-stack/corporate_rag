# ADR-0016：统一 agent 循环到 create_agent（L1/L2 参数化，领域阶段留外层图）

- **Status**：Accepted
- **Date**：2026-09-29
- **Deciders**：用户（决策）；Claude（调研、实现与评审）
- **关系**：`AgentSpanMiddleware` 的 `agent_turn` generation 沿用 ADR-0015 定的命令式 Langfuse client 接入方式；本 ADR 只改变该 observation 的**产生位置与可得的字段**（见「后果」），不改变其接入方式与保留策略。

## 背景与问题

系统里长期并存**两套 agent 循环实现**：

| 循环 | 承载 | 装配方式 |
|---|---|---|
| 主 agent 主循环 | 外层自建 `StateGraph` 的 `agent ↔ tools` 节点 + `route_agent` 条件边 | 手写 model 节点 / tools 节点 / 迭代计数 |
| fork 子代理 | `create_agent` 装配产物 | 一行 `create_agent(model, tools, system_prompt=…)` |

两者行为面高度重叠（model↔tools 往返、回合上限、工具结果回填），却在**装配、回合预算、观测、工具取数**四处各写一份，任何一处规则变更都要改两遍，且极易漂移。同一变更里已经出现过两处真实分叉：

- 工具经 `InjectedState` 取图状态时，自建图注入 `AgentState` **实例**、`create_agent` 注入 **`dict`** ⇒ 直接属性访问在 `create_agent` 下抛 `AttributeError`、被错误回喂吞成普通 `ToolMessage`，**检索恒空、澄清恒失败而图照常跑完**（见需求池 F-36）。
- 回合上限、温度分档、system 施加各自散落在节点函数与调用点内联参数里，无法被单一断言守住。

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| A 维持两套 | 主循环继续手写，子代理继续 `create_agent` | 每处规则改两遍；取数/预算/观测持续漂移 | 零改动 |
| B 主循环也自建 `StateGraph` 复刻 `create_agent` | 把子代理也换成自建图，统一到自建形态 | 要自行实现 middleware 语义（system 施加、回合预算、jump_to）；脱离 langchain 官方主线，升级脆弱 | 形态统一 |
| C **统一到 `create_agent`（本决策）** | 抽出唯一装配入口 `build_agent`，主/子角色共用；差异仅由参数提供 | 循环本体进入黑盒，观测层拿不到部分内部时刻（见「后果」） | 与官方主线一致；装配/预算/观测/取数各只有一处 |

## 决策

**选 C。** 具体形态：

1. **唯一装配入口** `src/agents/graph/agent_factory.py` 的 `build_agent(model, tools, *, system, max_turns, middleware_extra)`——`src/agents/` 下**唯一**允许调用 `create_agent(` 的地方（由 `tests/agents/graph/test_agent_factory.py` 的静态扫描断言守住）。主角色与 fork 子代理都经它装配，**差异只由参数提供**：
   - `system=None` = system 经运行态携带（主角色）；`system=<str>` = 静态串（子角色）。
   - `max_turns` = 回合上限的**唯一来源**，非 `None` 时由本函数自行追加 `AgentTurnBudget`。
   - `middleware_extra` = 额外 middleware 集合（子角色传空列表）。
2. **循环 middleware 四件套**（`src/agents/graph/middleware.py`）：`SystemMessagesMiddleware`（施加 system 段）/ `ModelParamsMiddleware`（温度分档 + 思考开关）/ `AgentTurnBudget`（回合上限，仅主角色）/ `AgentSpanMiddleware`（观测，**必须在最内层**，由 `build_agent` 装配期守卫）。
3. **领域阶段留外层图**：model↔tools 循环本体内化进装配产物；`agent_finalize` / `verify` / `format` 等**循环后阶段**仍在外层自建图。外层 `agent` 节点只负责「组装首轮消息 → 按类型拆 system / 非 system 两半 → seed 子图 → `ainvoke` → 按外层已有条数回写」。
4. **子图 schema 显式声明**：`LoopState`（`AgentState` 子类）承载子图六键；**默认值不填充**，消费者依赖的键由 `agent` 节点显式 seed。

## 理由

一句话：**「怎么跑循环」是通用能力，「跑完循环做什么」是本项目领域逻辑**——前者应只有一个实现、与官方主线一致；后者（答案提取、完整性校验、引用格式化）留在外层图，才能继续用本项目的自建拓扑表达。

把顺序不变量写成**装配期守卫**（`AgentSpanMiddleware` 必须在最后）比"靠调用方按约定排"强：它是可断言的硬约束，而不是文档里的一句话。

## 后果

**正面**：

- 装配 / 回合预算 / 温度分档 / 观测 / 工具取数各只有**一处**实现，改一处即全域生效。
- 主循环与子代理行为面由同一入口保证一致；「唯一装配」由静态扫描断言守住。
- 与 langchain 官方 `create_agent` 主线对齐，middleware 语义（`jump_to`、`state_schema`、hook 顺序）由框架承担。

**负面 / 接受的代价**：

- **观测退化（TTFB 丢失）**：`agent_turn` generation 现走 `AgentSpanMiddleware` 内的**命令式 span**；`awrap_model_call` 只能在模型调用**整体结束后**拿到 `ModelResponse`，**拿不到首 chunk 时刻** ⇒ `completion_start_time`（TTFB）**不再设置**。此前主循环的 `@observe` 包裹点能记该字段（曾用于区分"排队/首字节慢"vs"生成慢"）。`AgentSpanMiddleware` 保留了 `turn_start`（整体延迟），故 `latency` 不丢，**只丢 TTFB**。若要恢复须引入 callbacks 钩子取首 chunk，超出本变更范围。
- **`agent_node` 职责变化**：从"循环实现（model/tools 节点 + 路由 + 迭代计数）"收窄为"首轮组装 + 循环包装节点"。原 `make_agent_model_node` / `make_agent_tools_node` / `route_agent` 及 `AgentState._agent_iterations` / `_max_agent_iterations` / `_delegate_used` 三个循环字段随之消失；regen 的循环预算复位只剩 `ctx.web_count = 0`（回合计数每次 invoke 从字面初值起，无需复位）。

**不解决的问题**（明确列出，避免后人误以为本条管了它）：

- 触顶时"空回答却置 `status=complete`"的**用户可见缺陷**不修，归 `agent-round-budget`（见需求池 F-37）；本条只把该语义钉住（`agent-loop-observability` 要求 + 断言）。
- `AgentState` 的四个死字段（`timings` / `_token_usage` / `model_used` / `is_fallback`）不在本条范围（需求池 D-09）。
- 工具取数从属性访问改为**显式形状判定**只是随本决策同步的适配，其缺陷类别归属见需求池 F-36。

## 复查触发条件

- Langfuse 或 langchain 升级到 middleware API 语义变化的版本（`state_schema` / `hook_config` / hook 顺序）。
- 需要恢复 TTFB 观测（届时评估用 callbacks 钩子取首 chunk 的方案）。
- 出现"某条规则只能在外层图实现、但循环内也需要"的新需求（届时重新评估领域阶段与循环本体的边界）。
- 需要给 fork 子代理装配回合预算（届时经 `max_turns` 参数，而非另开装配路径）。
