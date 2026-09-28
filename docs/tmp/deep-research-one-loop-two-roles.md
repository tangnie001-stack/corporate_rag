# Deep Research：一循环两角色 —— 主/子 Agent 循环统一的技术路线

> 调研日期：2026-09-28　深度：Thorough
> 服务对象：`docs/tmp/one-loop-two-roles-brief.md` 第九节「待决清单」第 1 条
> 方法：Firecrawl 抓取 15 个一手页面 + 本地源码逐条核对两份参考实现 + 在本仓 venv（langgraph 1.2.9）上跑决定性探针

---

## Executive Summary

三个问题都有明确答案，且其中一条推翻了本简报的关键前提。

**第一，官方立场不是「推荐自建 StateGraph」。** LangChain 1.0 发布说明原文：`create_agent` 是「构建 agent 的标准方式」，它取代 `langgraph.prebuilt.create_react_agent`。官方文档把「一个 agent」定义为「模型在循环里调用工具」，把「harness（外壳）」定义为「循环周围的一切：prompt、工具、以及任何塑造模型行为的 middleware」——`create_agent` 被明确定位为「一个高度可配置的 harness」。自建 `StateGraph` 是官方**同样支持、同样教学**的路径，但它的官方定位是 **workflow 形状**（预定代码路径）与**需要细粒度控制**的场景，不是 agent 循环的默认答案。

**第二，「一套循环、两种角色」在两家参考实现中都成立，且证据是源码级的。** claude-code：`src/query.ts:276` 定义唯一的 `query()` 异步生成器，主线程（`QueryEngine.ts`、`screens/REPL.tsx`）与子代理（`AgentTool/runAgent.ts:776`）都消费同一个它；差异由 `utils/forkedAgent.ts:342` 的 `createSubagentContext()` 提供——文档串明写「默认隔离全部可变状态，显式 opt-in 才共享」。deepseek-harness：根 agent（`api/session-controller/src/agent.ts:477`、`bundle/headless/src/index.ts:183`）与子 agent（`subagent/subagent-in-process-driver/src/index.ts:134`）走**同一个** `ctx.agents.create()` 工厂，循环本体是 `core/agent-loop/src/agent.ts`；父子差异由 `applyChildComposition(childCtx, parent, { persona, toolFilter })` 这类显式参数提供。

**第三（最关键，且是实证而非推断）：要看到子代理内部，既不需要自建 StateGraph，也不需要把子代理做成子图节点。** 我在本仓 venv 上跑了探针：主 agent 用 `create_agent(name="probe-main")`，子代理用 `create_agent(name="probe-sub")` 包装成一个工具。结果（1）父级 `astream_events` 里**确实出现了子代理的内部事件**，并按 `metadata.lc_agent_name` 区分（`('probe-sub','model') 4, ('probe-sub','tools') 2` 对 `('probe-main', ...)`）；（2）langgraph 1.2 的 `stream_events(version="v3")` 的 `stream.subgraphs` 投影能直接列出它：`('probe-sub', ('tools:977cea00-…',))`，路径段就是派发它的工具节点。⇒ **本仓「子代理不可见」的根因不是「两套 loop」，而是 `src/agents/skills/executor.py:189` 那行 `var_child_runnable_config.set(None)` 主动切断了回调传播。** 这一条把「为了观测必须统一循环」的因果链拆掉了。

---

## Key Findings

### 1. 官方立场：`create_agent` 是标准，自建 `StateGraph` 是并列支持的另一条路

- LangChain v1 发布说明：「`create_agent` 是 LangChain 1.0 中构建 agent 的标准方式。相比 `langgraph.prebuilt.create_react_agent`，它提供更简单的接口，同时通过 middleware 提供更大的定制潜力。」
- 同页讲清了底层机制：「`create_agent` 构建在**基础 agent 循环**之上——调用模型、让它选择要执行的工具、在没有更多工具调用时结束。」
- 官方 agents 页把它上升为范式定义：「agent 是模型在循环里调用工具直到任务完成。**harness 是那个循环周围的一切**：prompt、工具、任何塑造模型行为的 middleware。」`create_agent` = 「高度可配置的 harness」。
- LangGraph 的「Workflows and agents」页（官方教学）确实手把手教自建 `StateGraph` 循环，但该页开宗明义区分：「workflow 有预定的代码路径；agent 是动态的、自己定义流程」。其 Agents 章节**不再给实现，而是指向** LangChain 的 agents 页。

**读法**：官方没有「自建更正确」或「prebuilt 更正确」的排序，只有**按问题形状选**。自建图的官方适用面是「workflow 形状 / 需要细粒度控制 / 要静态可见的子图结构」，不是「agent 循环本身」。

### 2. `create_agent` 本身就是一个可参数化到底的 `CompiledStateGraph`（本地实证）

在本仓 venv 上探针：

- `type(create_agent(...))` → `langgraph.graph.state.CompiledStateGraph`；节点名 `['__start__','model','tools','__end__']`；条件边 `model → {tools, __end__}`、`tools → model`。
- 可用的参数化面（`langchain.agents.create_agent` 签名）：`system_prompt`、`middleware`、`response_format`、`state_schema`、`context_schema`、`name`、`checkpointer`、`store`、`transformers`、`cache`。
- middleware 钩子（`AgentMiddleware`）：`before_agent` / `before_model` / `after_model` / `after_agent` / `wrap_model_call` / `wrap_tool_call`（各含 async 变体）。
- 官方提供了 `@dynamic_prompt`（走 `wrap_model_call`）——**这正是简报第四节说的「prompt 来源要参数化」的现成官方机制**；`ModelRequest` 暴露 `messages` / `system_message` / `tools` / `state` / `runtime` / `model_settings`，并可 `request.override(tools=…, model_settings=…)`。
- 预置 middleware 直接命中我们的既有功能：`ModelCallLimitMiddleware`（迭代上限）、`SummarizationMiddleware`（压缩）、`HumanInTheLoopMiddleware`（人工介入）。

### 3. claude-code：确认为「一套循环、两种角色」（源码级）

| 项 | 证据 |
|---|---|
| 唯一循环 | `src/query.ts:276` `export async function* query(params: QueryParams)` |
| 主线程用同一函数 | `src/QueryEngine.ts:36`、`src/screens/REPL.tsx:269`、`src/tasks/LocalMainSessionTask.ts:22` 均 `import { query } from './query.js'` |
| 子代理用同一函数 | `packages/builtin-tools/src/tools/AgentTool/runAgent.ts:776` `for await (const message of query({ messages, systemPrompt, userContext, systemContext, canUseTool, toolUseContext, querySource, maxTurns }))` |
| 差异来自上下文派生 | `runAgent.ts:709` `createSubagentContext(toolUseContext, {...})`；定义在 `src/utils/forkedAgent.ts:342` |
| 隔离哲学（原文注释） | 「默认隔离全部可变状态以防互相干扰」「调用方可通过 overrides 覆盖特定字段，或显式 opt-in 共享特定回调」 |

`createSubagentContext` 的具体语义与我们的「七轴」几乎一一对应：

- **回合上限是参数**：`maxTurns: maxTurns ?? agentDefinition.maxTurns`。
- **身份**：`agentId: createAgentId()`（子代理各自新 id）。
- **血统**：`queryTracking: { chainId: randomUUID(), depth: (parent.depth ?? -1) + 1 }`。
- **取消传播**：`abortController = createChildAbortController(parent.abortController)`（父取消传到子）。
- **UI 不外溢**：`addNotification / setToolJSX / setStreamMode / setSDKStatus` 一律 `undefined`。
- **隔离项**：`readFileState` 克隆、`toolDecisions` 全新、`setAppState` 默认 no-op、`localDenialTracking` 独立。
- **脑（模型档位）也是参数**：子代理默认 `thinkingConfig: { type: 'disabled' }`（fork 路径除外）。

### 4. deepseek-harness：确认为「一套循环、两种角色」（源码级）

| 项 | 证据 |
|---|---|
| 同一 agent 工厂 | 子代理：`packages/subagent/subagent-in-process-driver/src/index.ts:134` `await parent.ctx.agents.create({ sessionId: childId, meta: childSessionMeta(parent, childDepth, seed !== undefined), agentOptions: resolveChildAgentOptions(parent, …), signal, setup })` |
| 根 agent 用同一工厂 | `packages/api/session-controller/src/agent.ts:477`、`packages/bundle/headless/src/index.ts:183` 同样 `await this.ctx.agents.create({…})` |
| 循环本体 | `packages/core/agent-loop/src/agent.ts`（「Default Agent driver over queued turns and step-boundary input」） |
| 继承接缝 | `applyChildComposition(childCtx, parent, { persona, toolFilter })`、`captureDelegatedPolicyOverrides(parent)` / `appendDelegatedPolicyOverrides(...)` |
| 谱系/深度 | `resolveChildDepth(parent, request.maxDepth)`、`childSessionMeta(parent, childDepth, …)` |
| 独立会话 | `sessionId: childId = randomUUID()`（子代理跑在独立 Session，符简报） |
| 生命周期 | 驱动只拥有「一个 turn 一个 result」，`child.followup(...)` → `await child.whenIdle()`，`TurnEndReason` 映射为终止词表（`completed`/`max-tokens`/`aborted`/`refusal`/`error`） |

### 5. 【决定性】子代理可见性不需要统一循环 —— 探针实证

本仓 langgraph 1.2.9 上，主 `create_agent(name="probe-main")` + 工具内调用 `create_agent(name="probe-sub")`：

```
V2 astream_events（按 metadata 统计）：
  ('probe-main', 'model') 10   ('probe-main', 'tools') 5   ('probe-main', None) 5
  ('probe-sub',  'model')  4   ('probe-sub',  'tools') 2
V3 stream_events(version="v3").subgraphs:
  [('probe-sub', ('tools:977cea00-2d5f-4ab6-77e6-947079b71a5b',))]
```

三条结论：

1. **回调会自然传播**：子代理在工具内被调用时，其内部事件正常出现在父级的 `astream_events` 里。本仓之所以看不见，是因为 `executor.py:189-190` 显式 `var_child_runnable_config.set(None)` 把传播掐断了（该处注释自陈目的就是「隔离子代理回调传播……reset 后子代理事件只走其自身 handler」）。
2. **判别键是现成的**：`metadata.lc_agent_name` 携带 `create_agent(name=…)` 设的名字。这正是简报「七轴·观测」要求的「子内部步骤归子线、带 `subagent=` 判别（环境注入）」，且**由框架自动注入**，不用自己拼。
3. **`stream.subgraphs` 能看见工具派发的命名 agent**：官方文档原话——「一个从工具派发的命名 agent（例如通过 Deep Agents `task` 工具调用的 `create_agent(name=...)`）会以该名字出现在这里，且开启该作用域的 `lifecycle` 事件带有回指派发工具调用的 `cause`」。这一点**修正**了官方另一处「subagents 在工具函数里被调用，LangGraph 无法静态发现它们」的说法——那句限制只适用于 `get_state(subgraphs=True)` 的**状态**检查，不适用于**事件**观测。

### 6. 「一循环两角色」在官方已被产品化：LangChain subagents 模式 + Deep Agents

- 官方 multi-agent/subagents 页：主 agent（supervisor）通过把子代理**当作工具**来调用。「默认子代理是无状态的——它们不记得过去的交互，全部对话记忆由主 agent 维护。这提供 **context 隔离**：每次子代理调用都在干净的 context window 里工作」。并明说：「子代理可能与主 agent 能力完全相同。这种情况下，调用子代理**主要就是为了 context 隔离**」——这正是我们 fork skill 的定位。
- 实现形态就是**两边都用 `create_agent`**：`subagent = create_agent(...)`，`@tool def call_subagent(...): return subagent.invoke(...)`，`main_agent = create_agent(tools=[call_subagent])`。
- Deep Agents（官方产品化 harness）把这套参数化到字段级：子代理声明 `name / description / system_prompt / tools / model / middleware / skills / mode`。其中 `mode: "isolated" | "fork"` 精确对应我们的「继承轴」：`isolated`（默认，只见委派任务）vs `fork`（继承父的对话与 system prompt）。并明确 `system_prompt` 与 `skills`「不从主 agent 继承」（subagent 的 skill 状态与父完全隔离、互不可见）。
- 观测：官方说每个子代理的 `name` 会写进它产生的每个 run 的 `lc_agent_name` 元数据键，用于在追踪系统里过滤（LangSmith 语境）。
- 生命周期/边界上限：Claude Code 官方 SDK 文档同样有「Cap subagent depth, concurrency, and spend」，与 our 七轴第 2/7 条一致。

### 7. 观测语义有两条权威先例，且结论相反 —— 必须显式选一条

- **claude-code 路线（独立 trace，共享 sessionId）**：`runAgent.ts:752-762` 为子代理 `createSubagentTrace({ sessionId: getSessionId(), agentType, agentId, model, … })`，注释写明「Sub-agent trace shares the same sessionId as the parent, so Langfuse groups them under the same Session view」；`runAgent.ts:770` 把它挂到 `toolUseContext.langfuseTrace`，`query.ts:293-296` 检测到已存在就**复用而不新建**（「When called as a sub-agent, langfuseTrace is already set by runAgent() — reuse it instead of creating an independent trace」）。
- **Langfuse 官方文档路线（单 trace + 嵌套 span）**：其 LangGraph 集成文档专设 Example 5「Nested LangGraph agents in one trace」——「一个 LangGraph agent 使用一到多个其他 LangGraph agent 时，要把它们对应的 span 合到**单个 trace** 里，我们可以传一个自定义 `trace_id`」，并用 `trace_context={"trace_id": predefined_trace_id}` 让子代理挂进同一 trace。

⇒ 简报第五节把「Langfuse 侧独立 trace vs 子 span」列为未决是**对的**，而且这两条都有权威背书，选哪条是**产品取舍**不是对错：独立 trace 便于按 subagent 单独打分/筛选，单 trace 嵌套便于看整体因果链与成本汇总。

### 8. context 隔离的代价有硬数据

- Anthropic：多智能体架构「比单 agent 高出 90.2%」（内部研究评测），但「agent 通常比 chat 多用约 4× token，多智能体系统比 chat 多用约 **15×** token」；「有些需要所有 agent 共享同一上下文的领域不适合多智能体」。
- LangChain：subagent 模式的「关键取舍是**每次交互多一次模型调用**，因为结果必须流回主 agent。这个开销换来实现集中控制与 context 隔离，代价是延迟与 token」。
- 原生 CLI 观测不可见的代价（本仓已付）：子代理内部步骤既不入本地日志也不入 Langfuse，只能靠 `delegate start/delta/end` 三个粗粒度事件对外。

### 9. 反例材料：Anthropic 明确警示「框架抽象会遮蔽底层」

- 「这些框架让入门变简单……但它们常常引入额外的抽象层，**遮蔽了底层的 prompt 与响应，使其更难调试**。它们也容易诱导你在更简单的方案已足够时增加复杂度。」
- 「我们建议开发者从直接用 LLM API 开始……**如果你确实要用框架，确保你理解底下的代码。对底层机制的错误假设是客户错误的常见来源**。」
- 「只在复杂度被证明能改善结果时才增加复杂度。」

这条直接支持本仓**保留自建循环**的取向（本仓的 `agent_node.py` 里塞了大量领域逻辑：kb 温度分档、prompt 段条件注入、delegate 上限放宽、Langfuse generation 手写 span）。若把主图整换成 `create_agent`，这些要么进 middleware（黑盒化），要么进外层图（等于没换）。

---

## Detailed Analysis

### A. 三个层次的问题，别混成一句「统一到哪套 loop」

简报第 1 条问的是「统一到哪套 loop」，但调研显示至少有**三个可独立决定**的层次：

| 层次 | 问题 | 官方默认答案 | 本仓现状 |
|---|---|---|---|
| L1 循环本体 | model ⇄ tools 的迭代机制由谁实现 | `create_agent`（1.0 标准） | 主图自建、子代理 prebuilt（**不一致**） |
| L2 harness（循环周围） | prompt 组装 / 温度 / 迭代上限 / 工具面 / 压缩 | middleware + 参数 | 散在节点函数里 |
| L3 领域阶段 | verify / format / agent_finalize / skill_direct | 无官方对应物（**本仓的差异化资产**） | 挂在同一张图里 |

**关键判断：真正值得「统一」的是 L1+L2，L3 无论如何都该留在循环之外。** 而 L1 的统一**可以用极低成本达成**：让子代理和主 agent 都经由同一套 `create_agent` 装配函数（带 `name` / `system_prompt` 来源 / tools / middleware / 上限参数），——这与 LangChain 官方 subagents 模式完全同形，也与 claude-code / deepseek-harness 的「同一工厂、参数化差异」同形。

### B. 三条可选路线（含代价）

**路线 ①：保留自建主图，只把「循环 + 观测」两处修好（最小改动）**

- 删掉 `executor.py:189` 的 `var_child_runnable_config.set(None)`，改由 `metadata.lc_agent_name`（或自定义 tag）判别主/子事件。
- 给子代理 `create_agent(..., name=…)`，让事件带上可判别身份。
- 子代理仍单独消费事件流（或改为复用父流 + 过滤），但**判别键来自框架**。
- 代价最小；但 L1 仍是两套实现，`_build_sub_agent` 与 `make_agent_model_node` 各自演化。
- 风险：`set(None)` 之所以存在，是为了防「子代理 token 污染主 SSE + `full_answer` 累积子代理原文」（源码注释自陈）。**去掉它必须同时给 SSE 消费端加 `lc_agent_name` 过滤**，否则就是回归 bug。

**路线 ②：L1 统一到 `create_agent`（两边同工厂），L2 用 middleware，L3 留在外层图**

- 抽出**一个**「agent 装配工厂」：`build_agent(model, tools, prompt_source, name, max_turns, middleware…)`，主/子都调用它。
- 主图的外层 `StateGraph` 保留，但把 `agent ⇄ tools` 那段换成「装配好的 create_agent」——两种接法官方都写了配方：**state 不同 → 在节点函数里 invoke（可转换状态）**；**state 相同 → 直接 `add_node`（编译好的子图）**。
- prompt 来源参数化用 `@dynamic_prompt`：主角色读 RAG 组装（`build_prompt`），子角色读执行者人设 + 执行契约。
- 温度分档 / 额外 body / Langfuse generation span / 迭代上限 → `wrap_model_call` + `model_settings` / `ModelCallLimitMiddleware`（或自定义 middleware）。
- 观测：`stream_events(version="v3").subgraphs` 或 `astream_events` + `lc_agent_name` 过滤；一次接线两角色共用。
- 代价：SSE 转换层要改（节点名 `agent` → `model`），`observe(name="agent_turn")` 的手写 generation span 要重写为 middleware，`route_agent` 的 `MAX_DELEGATE_BONUS` 放宽逻辑要变成自定义 middleware。**这是简报「允许破坏性改动」下最贴近两家参考实现、且长期维护成本最低的形态。**
- 风险：v3 事件流在本仓版本上是 `LangChainBetaWarning: experimental`；`use-subgraphs` 文档另有警告——**per-thread 子图不支持并行工具调用**（同一 namespace 写冲突），若把子代理做成 per-thread 子图需自行禁止并行调用。

**路线 ③：主图整换成 `create_agent`（最彻底）**

- 收益：L1/L2 全交框架，L3 变成外层 graph 的兄弟节点。
- 代价高：本仓主图节点不只是循环，还承载 history 截断、`SKILL_INJECTION_PREFIX` 抽取、kb 温度分档、`tool_contexts`/`temporal_years` 装载、verify 条件回边、`skill_direct` 零 LLM 入口。全部要重写为 middleware 或外层节点，等于**在换底座的同时重写产品逻辑**。
- 与 Anthropic 的警示直接冲突（「遮蔽底层、更难调试」），且本仓是「企业级 harness」——外壳本身就是资产。

**综合建议（供第 1 条决策）**：**路线 ②**。它同时满足「一循环两角色」「两家参考实现的同形」与「保留 L3 领域资产」，并把观测接线从「为了统一循环」降级为「顺手完成」。若短期风险预算不足，先把路线 ① 的两处（删 `set(None)` + 加 `name` + SSE 加过滤）作为**独立可交付的小改动**落地——它单独就能解决「子代理不可见」这个根因，且与路线 ② 不冲突、是路线 ② 的第一步。

### C. 对简报七轴的映射校准（用官方机制替换自造机制）

| 七轴 | 官方/参考实现里的对应物 | 校准建议 |
|---|---|---|
| 1 身份 `subagent_id` | `create_agent(name=…)` → `metadata.lc_agent_name`；claude-code `agentId`；dsh `SessionId` | 用框架的 `name` 作为身份源，别自造单值字段 |
| 2 血统 parent/depth | claude-code `queryTracking.depth+1`；dsh `resolveChildDepth` + `childSessionMeta`；CC 官方「cap depth/concurrency/spend」 | 保持；并发时深度随运行态传递 |
| 3 继承白名单 | Deep Agents `mode: isolated|fork`；CC「What subagents inherit」表（不继承父 history / 父 system prompt / 父 tool results；继承 extended thinking 配置） | 直接照抄 `isolated|fork` 二分，比「8 个拍的字段」更可解释 |
| 4 隔离 | claude-code `createSubagentContext` 默认全隔离 + opt-in 共享；dsh 独立 Session | 保持；共享项只保留「事件通道 + 取消信号」 |
| 5 观测 | `lc_agent_name`（环境注入，免自造）；`stream.subgraphs`；CC 独立 trace 共享 sessionId；Langfuse 单 trace 嵌套 | **用框架注入的判别键**替换自造 `subagent=` 追加 |
| 6 生命周期 | dsh `TurnEndReason` → 终止词表；CC `maxTurns`/`abortController` 链 | 本仓 `DelegateStopReason` 已同形，保持 |
| 7 并发 | `use-subgraphs` 警告 per-thread 子图并行冲突；官方推荐 `ToolCallLimitMiddleware`；CC 有 concurrency cap | 若走路线 ② 的 per-thread 子图接法，**必须**同时上调用上限 middleware |

---

## Contrarian Views And Risks

1. **「统一」可能不是收益最大的那一步。** 本仓真正的痛点（子代理不可见）可被一条 3 行改动解决（删 `set(None)` + `name=` + SSE 过滤）。若把「统一循环」当成观测的前置条件，会背上一场大重构而收益不变。**先证伪「必须先统一」，再决定要不要统一。**
2. **框架抽象的调试代价是权威警示，不是我的猜测。** Anthropic 原文点名「遮蔽底层 prompt 与响应、更难调试」「对底层机制的错误假设是客户错误的常见来源」。本仓 Node 里的领域逻辑密度很高，迁移到 middleware 会把这些逻辑从「可读的 Python 函数」变成「注册在某处的回调」。
3. **15× token 的经济性门槛。** 若本产品要长期跑 fork 子代理，Anthropic 的数据说明这只有在「任务价值足够高」时才成立；也解释了为什么本仓给子代理设了 `DELEGATE_RESULT_LIMIT` 截断。
4. **v3 事件流是 experimental。** 本仓 langgraph 1.2.9 上跑 `stream_events(version="v3")` 会打 `LangChainBetaWarning`。若要依赖 `stream.subgraphs`，等于把生产可观测性绑在实验 API 上；稳妥做法是 `astream_events` + `lc_agent_name`（本次探针同样实证可用）。
5. **`set(None)` 的删除有回归风险，不是纯删除。** 它的存在是为了防止子代理 token 污染主 SSE 与 `full_answer`。删之前必须先让 SSE 消费端认 `lc_agent_name`，**并且要有针对「子代理 token 不得进入主回答」的测试**。
6. **官方文档内部有一处口径张力**：multi-agent/subagents 页说「subagents 在工具函数里调用，LangGraph 无法静态发现，`get_state(subgraphs=True)` 拿不到子代理状态」；而 event-streaming 页说命名 agent 经工具派发也能出现在 `stream.subgraphs` 里。**两者不矛盾**（前者讲状态检查、后者讲事件观测），但容易被误读成「工具派发就一定能看见一切」——若将来需要 `interrupt` 进子代理，官方明确要求改用「在自定义图的节点函数里 invoke 子代理」。
7. **本报告未验证的部分**：本仓真实图（带 verify/format/skill_direct、真实 DashScope 模型）上的行为未跑；探针用的是假模型与最小图。v3 在异步消费者下的正确用法我只跑通了同步迭代（异步迭代报了 `This StreamChannel is bound to sync mode`），异步用法需另行确认。

---

## Open Questions

1. **观测语义选哪条**：独立 trace 共享 sessionId（claude-code 先例）还是单 trace 嵌套 span（Langfuse 官方先例）？这直接决定本仓 Langfuse 接线与「详情」按钮的呈现。
2. **L1 统一的收益是否值得**：若删 `set(None)` + `name=` 就已解决根因，L1 统一还要不要做？（建议：先做前者，用一次真实 E2E 取证再决定。）
3. **是否允许把迭代上限 / 温度分档 / generation span 迁进 middleware**：这决定路线 ② 与路线 ③ 的可行性；也决定本仓「壳」的可读性。
4. **子代理材料是否回传父**（简报原未决项）：官方 subagents 模式给了第二方案——用 `Command(update={...})` 把结构化 state 一并回传，而不只是 final text；是否采用？
5. **v3 experimental 是否可接受**：若不可，`stream.subgraphs` 就要降级为 `astream_events` + `lc_agent_name` 过滤（已实证可行）。

---

## Sources

一手文档（LangChain / LangGraph 官方）：

1. https://docs.langchain.com/oss/python/releases/langchain-v1 — 「`create_agent` 是构建 agent 的标准方式，取代 `create_react_agent`」；middleware 是定义性特性；`create_agent` 建立在基础 agent 循环之上。
2. https://docs.langchain.com/oss/python/langchain/agents — 「agent 是模型在循环里调用工具；harness 是循环周围的一切」；`create_agent` = 高度可配置的 harness。
3. https://docs.langchain.com/oss/python/langgraph/workflows-agents — 官方教学：workflow vs agent；自建 `StateGraph` 的模式与 `ToolNode`；Agents 章节指向 LangChain agents 页。
4. https://docs.langchain.com/oss/python/langchain/multi-agent/subagents — 官方 subagent 模式：子代理 = `create_agent` 包成工具；默认无状态；context 隔离；`get_state(subgraphs=True)` 拿不到子代理状态；`Command` 回传结构化 state。
5. https://docs.langchain.com/oss/python/langchain/multi-agent — 多智能体四模式总览（subagents / skills / handoffs / routers）。
6. https://www.langchain.com/blog/choosing-the-right-multi-agent-architecture — 四架构的适用面与取舍；subagent 模式「每次交互多一次模型调用」。
7. https://www.langchain.com/blog/how-middleware-lets-you-customize-your-agent-harness — 「每个 agent harness 的核心都一样：LLM 在循环里调工具」；`before_model` / `wrap_model_call` 钩子语义与应用场景。
8. https://docs.langchain.com/oss/python/langchain/middleware/custom — 自定义 middleware 的写法与钩子清单。
9. https://docs.langchain.com/oss/python/langgraph/use-subgraphs — 子图两种接法（节点内 invoke / `add_node`）；命名空间隔离；**per-thread 子图不支持并行工具调用**；`get_state(config, subgraphs=True)`。
10. https://docs.langchain.com/oss/python/langgraph/event-streaming — v1.2 事件流；`stream.subgraphs` 观测嵌套执行；**命名 agent 经工具派发也会以该名字出现**，`lifecycle` 带回指派发工具的 `cause`；`namespace` 形如 `["researcher:6f4d","tools:91ac"]`。
11. https://docs.langchain.com/oss/python/langgraph/streaming — `messages` 模式逐 token 流出（含子图）；「新应用推荐事件流」。
12. https://docs.langchain.com/oss/python/deepagents/subagents — 子代理字段级契约；`mode: isolated|fork`；`system_prompt`/`skills` 不继承；`lc_agent_name` 追踪过滤；最佳实践（描述要清楚、工具集要小、system prompt 要详细）。
13. https://docs.langchain.com/oss/python/deepagents/overview — Deep Agents 与 `create_agent` 的分工（前者装配好规划/文件/子代理/记忆）。
14. https://docs.langchain.com/oss/python/langchain/context-engineering — 官方 context 工程立场（middleware 控制动态 prompt / 摘要 / 选择性工具访问）。

参考实现与行业实践：

15. https://code.claude.com/docs/en/agent-sdk/subagents — 「Context isolation：每个子代理跑在自己的会话里，除非是 fork，否则全新开始；中间工具调用与结果留在子代理内，只有最终消息回到父级」；inherit 表；子代理可再派子代理，有 depth/concurrency/spend 上限。
16. https://www.anthropic.com/engineering/building-effective-agents — 「找最简单的方案，只在需要时增加复杂度」；框架「遮蔽底层 prompt 与响应、更难调试」；「确保你理解底下的代码」。
17. https://www.anthropic.com/engineering/multi-agent-research-system — 多智能体 +90.2%；多智能体约 15× token；「加完整生产追踪才让我们诊断出 agent 为何失败」。
18. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents — context 工程视角（长时程 agent 的上下文管理）。
19. https://langfuse.com/integrations/frameworks/langgraph — Example 5「Nested LangGraph agents in one trace」：传自定义 `trace_id` 把多 agent 的 span 合成单 trace。
20. https://arize.com/blog/context-management-in-agent-harnesses/ — harness 层 context 管理的业界经验（对照参考）。

本仓/本地源码（逐条核对，行号即证据）：

21. `../github/claude-code/src/query.ts:276`（唯一循环）、`:293-296`（子代理复用父 trace 不新建）、`src/QueryEngine.ts:36`、`src/screens/REPL.tsx:269`、`packages/builtin-tools/src/tools/AgentTool/runAgent.ts:709/752-762/770/776-785`、`src/utils/forkedAgent.ts:303-470`（`createSubagentContext` 的默认隔离与 opt-in 共享）。
22. `../github/deepseek-harness/packages/subagent/subagent-in-process-driver/src/index.ts:134`、`packages/api/session-controller/src/agent.ts:477`、`packages/bundle/headless/src/index.ts:183`、`packages/core/agent-loop/src/agent.ts`、`packages/subagent/README.md`（provider 家族）。
23. 本仓 `src/agents/skills/executor.py:189-190`（`var_child_runnable_config.set(None)`，观测断链根因）、`src/agents/skills/fork_stream.py`（自造事件消费）、`src/agents/graph/workflow.py:72-133`、`src/agents/graph/agent_node.py:148-317`、`src/infra/llm/request_context.py:78-98`（`child()`）。
24. 本机探针（2026-09-28，langgraph 1.2.9 / langchain 1.3.11）：`create_agent` 编译图为 `CompiledStateGraph`，节点 `model`/`tools`；命名子代理事件在父级 `astream_events` 可见且 `metadata.lc_agent_name` 可判别；`stream_events(version="v3").subgraphs` 返回 `('probe-sub', ('tools:…',))`。

---

## Rerun Inputs

```
workflow: firecrawl-deep-research
topic: 一循环两角色 —— 主/子 Agent 循环统一（自建 StateGraph 参数化 vs 统一 create_agent）
depth: thorough
output: markdown
constraints: 中文；落 docs/tmp/；需含本地源码核对与本仓探针实证
```
