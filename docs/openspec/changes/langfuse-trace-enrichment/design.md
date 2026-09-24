## Context

`llm-tracing` 已随 change `langfuse-trace-wiring`（2026-09-24 归档）成为主规格：每轮对话一条 trace、每轮 LLM 推理一条 `agent_turn` generation、id 四方对齐、保留期清理。本变更负责它的**内容补全**。

**现状（dev 库 `trace_19e8e472-…` 实测）**

- `observations` 里只有 `agent_turn` 一种（GENERATION）；**没有根 span**（`traces` 有 `chat_turn`，但 `observations` 无对应行），也没有任何工具类观测。
- 工具结果之所以「看得见」，是 `role="tool"` 的 ToolMessage **被卷进下一轮 generation 的 `input`**（逐轮累积 0/1/2/3/4 条）；工具轮的 `output` 是 **NULL**。
- `input` 的消息 `role` 是 **LangChain 类型名**（`ai` / `human` / `system` / `tool`），不是 OpenAI 形态。
- `traces.user_id` / `tags` 空、`metadata` 基本 `{}`。
- 成本空：`models` 表只有 OpenAI 系列定价，无 `qwen3.8-flash`。

**已实测的约束（`langfuse==2.60.10` / `langgraph==1.2.9` / `langchain-core==1.4.8`，容器内一手核实）**

| 事实 | 影响 |
|---|---|
| `@observe` 的 `as_type` 只接受 `"generation"`；**span 是「有 parent 时的默认类型」** | 不能用 `as_type="span"` 显式声明 |
| **没有** `start_as_current_span` 之类的上下文管理器 | start/end 分离的 span 只能走命令式 client |
| 顶层（无 parent）的 `@observe()`（非 generation）会 `client.trace(**params)` | **禁止**在无 parent 处这样用（会 upsert / 改写 trace） |
| `Langfuse.span(trace_id=…, parent_observation_id=…, …)` 存在，返回 `StatefulSpanClient`，有 `.end()` | 工具 span 的实现基础 |
| `StatefulClient.span()` 只入队 `span-create`，**不**重发 `trace-create` | 挂 span 不会动 trace |
| 命令式 `client.trace(id=…)` 只传 id 时，`name`/`input`/`output`/`metadata`/`tags` **不被清空**（服务端 `?? undefined` 过滤），但 **`timestamp` 被无条件覆盖** | 见 D3：干脆不调它 |
| `on_tool_start.data.input` = 工具实参 dict，**不含 `tool_call_id`**；`on_tool_end.data.output` 是 **`ToolMessage` 对象**（含 `.content`/`.tool_call_id`/`.name`）；`on_tool_error.data` 才直接带 `tool_call_id`；三者 `run_id` 相同 | 配对与字段提取的依据 |
| **`on_tool_*` 事件的 `metadata.checkpoint_ns` 是 `tools:<uuid>`（非空）**；而 `on_chain_start/end(tools)` 的 `checkpoint_ns` 是 `None` | 见 D4：**不能**用 `checkpoint_ns` 判空来过滤子图 |
| 一个 `langgraph_step` = 一次 Pregel 超步；`agent` 与 `tools` 必然相邻但不同 step；**同一 step 内多个工具并行** | 见 D2（改用节点事件做锚点） |
| 节点级 `on_chain_start/end` 事件存在（既有代码已在消费 `name=="format"/"agent_finalize"/"skill_direct"`） | 父 span 的锚点 |
| `graph.astream_events(...)` 已在 `_run_generation` 的循环里被 `_convert_event` 消费（SSE sink） | 复用点 |
| **fork 子代理的回调传播被显式切断**：`src/agents/skills/executor.py:189` 执行 `var_child_runnable_config.set(None)`（注释明写「不 reset 会…子代理 LLM 事件泄漏到外层 `graph.astream_events`」），子代理改由自己的 `sub_agent.astream_events(...)`（`src/agents/skills/fork_stream.py:114`）消费 | 子代理的工具 / LLM 事件**根本不进主图事件流** —— 这是 fork 不覆盖的**真实原因**（**不是**被 `scope` 过滤，见 D9） |

**数据源**：`RequestContext` 已携带 `agent` / `agent_display_name` / `loaded_skills` / `skill_action` / `kb_id` / `kb_domain` / `deep_thinking` / `has_skills`，无需新增取数。用户身份由认证中间件（`src/middleware/auth.py`）设置的 `current_user_id` 提供，但**取用方式见 D6**。

## Goals / Non-Goals

**Goals:**

- 每条工具调用成为一条可点、可读、有耗时的 Langfuse span（工具名 / 入参 / 返回 / 错误态），且同一轮的多次调用归入该轮的 `tools` 父 span。
- 工具轮的 generation 不再空白：`output` 含模型声明的 `tool_calls`；`input` 的消息可读（OpenAI 形态 role + assistant 的 `tool_calls` + tool 的 `name`）。
- trace 可按人与业务维度切分：写入 `user_id`、低基数 `tags`、业务 `metadata`。
- `qwen3.8-flash` 的成本可见（单价可配置，未配置时不产生误导性的 0）。
- **对 MCP 零改动兼容**：新增工具只要从 `tool-registry` 统一入口进 ToolNode，采集器不需要改。

**Non-Goals:**

- **scores 回写**（`feedback → score`、RAGAS → score）—— 另案。
- **CLI 链路**（`eval_ragas` 走 `graph.ainvoke`，没有事件流）—— 本期不接，登记为遗留。
- **fork 子代理的工具调用**：子代理事件**不进主图事件流**（原因见 Context 表与 D9）。将来若要覆盖，需在 `fork_stream.py:114` 的子代理事件流上再挂一个同类消费者，并单独裁定子代理 span 的 trace 归属 —— 属另一个变更。
- **工具内部子步骤**（embed / 向量检索 / rerank）：它们不是 LangChain Runnable，图形事件流里没有它们的事件。
- **根 span 重构**：`agent_turn` / `tools` 仍是 trace 的顶层节点（根是 trace 而非 span），本变更不改变这一点。
- 不新增第三方依赖。

## Decisions

### D1 工具观测走「事件流驱动」（方案 D），而非节点包裹（A）或逐工具装饰（B）

| | A 每轮 `tools` span | B 逐工具装饰器 | **D 事件流驱动** |
|---|---|---|---|
| 取数来源 | 图节点 `state` | 工具函数进出参 | `astream_events` 的 `on_tool_*` |
| 粒度 | 一轮一条（多工具合并） | 一次调用一条 | **一次调用一条** |
| 逐工具耗时 | ❌ | ✅ | ✅ |
| 覆盖 `delegate_task` 本身 | ✅ | ❌（要单独改） | ✅ |
| 复用 SSE 同一批事实 | ❌ | ❌ | ✅ |
| 对 MCP 工具 | 自动 | **覆盖不到**（适配器产出的工具无法逐个加装饰器） | **零改动** |
| 新机制 | 无 | 无 | 命令式 API（见 D3，需 ADR） |

**理由**：D 的数据是 A+B 的超集（多出逐工具耗时、`run_id` 关联、结构化错误事件），且**唯一复用既有事件流**；对「工具实现」无感这一点在 MCP 接入后价值最大。代价是引入命令式 API，用一条 ADR 记清与既有「纯装饰器」决策的取舍。

### D2 父 span 锚点用节点级 `on_chain_start/end`（`name == "tools"`），不用 `langgraph_step`

`step` 是超步号，`agent` 与 `tools` 各占一步、同一 `tools` 节点内多个工具**并行且同 step** —— 拿它归组要额外维护「当前是第几轮」的映射。而节点级 chain 事件天然标出 `tools` 节点的**进出**：`on_chain_start(name=="tools")` 开父 span、`on_chain_end(name=="tools")` 关父 span。语义更直白，也不依赖 step 语义。

### D3 用命令式 `Langfuse.span(trace_id=…)` 建 span，**完全不调用 `client.trace()`**

工具 span 需要 start / end 分离，装饰器表达能力不足（见 Context 表）。命令式路径的关键取舍是：

- 若用 `client.trace(id=…)` 拿句柄再 `.span()`：SDK 会随请求带上 `name/input/output = null` 与 `timestamp = now`；服务端对 `name/input/output` 用 `?? undefined` 过滤（**不会清空**），但 **`timestamp` 会被无条件覆盖** —— 每建一次 span 就把 trace 的开始时刻往后推。
- `Langfuse.span(trace_id=…)` 直接以显式 `trace_id` 建 span，**不触碰 trace 行**，`timestamp` 从根上不会被改；且 `parent_observation_id` 可显式指定，父子关系完全可控（不依赖 contextvar 栈）。

**决策**

- 只用 `Langfuse.span(trace_id=…)`，**永不调用 `client.trace()`**。这同时把「命令式路径是否会改写根 trace」这一整类风险消除。
- **client 实例统一取 `langfuse_context.client_instance`** —— 它与 `configure_tracing()` / `flush_tracing()`（`src/infra/llm/tracing.py`）管的是**同一个单例**。**不得** `new Langfuse()`：那会绕过 `LANGFUSE_ENABLE` 开关、也不被关停时的 `flush` 覆盖，后果是「禁用态仍可能出网」+「关停丢 span」。

### D4 `_ToolTraceCollector` 的形态

与 `_StreamCapture` 平级：**请求内私有**（每次请求 new 一个，绝不共享，避免并发串账）、**跨事件累积状态**、**自带行为**。

- `_enabled`：从 `settings.LANGFUSE_ENABLE` 取，命令式路径未必受 `configure(enabled=False)` 管，必须自己断电。
- `_trace_id`：`current_trace_id.get()`，显式传给 `Langfuse.span`。
- `_open: {run_id: span}`：start 存入、end/error 取出 —— **配对账本**。
- `_round` / 「当前 tools 父 span」：`on_chain_start/end(name=="tools")` 开合。
- `consume(item)`：`on_tool_start` / `on_tool_end` / `on_tool_error` 三分支；其余事件直接返回。
- `close()`：**收尾兜底**，把仍未关闭的 span 全部 `end()`。取消 / 异常会走 `finally`，不调则 span 悬空（有开始无结束）。
- 只在 `scope == "main"` 时消费（与 `_convert_event` 同口径）。
- **过滤判据只用 `metadata.langgraph_node == "tools"`**（与 `_convert_event` 的既有口径一致）。**不得用 `checkpoint_ns` 判空来排除子图** —— 实测 `on_tool_*` 的 `checkpoint_ns` 是 `tools:<uuid>`（**非空**），照此过滤会**丢掉全部工具事件**，本变更主目标直接失败。
- **不按工具名分支、`data.output` 原样透传** —— 这是 MCP 兼容与「不许退化成 B」的不变量。

### D5 generation 载荷：`output` 文本优先；`input` 的 role 规范化

- `output`：文本非空时仍写文本（**不动最终答案那一轮的渲染**）；文本为空且存在 `tool_calls` 时写 `{"tool_calls": […]}`。
- `input`：`_messages_payload` 的 `role` 由 `m.type` 映射为 OpenAI 形态（`ai`→`assistant`、`human`→`user`，`system`/`tool` 原样）；assistant 条目补 `tool_calls`（name + args），tool 条目补 `name`。
- 仍然 `capture_input=False` + 显式写入，**不得**把 `InjectedState` / `ctx` / `manager` 等内部对象带进 trace（既有 `llm-tracing` 要求的「内部运行时对象不进 trace」）。

### D6 trace 富化：字段、tags 口径、以及 `user_id` 的取用方式

`update_current_trace(input=…, session_id=…, user_id=…, tags=…, metadata=…)`：

- `user_id`：**在请求内捕获后显式传入**，不依赖 contextvars 继承。实测 `asyncio.create_task` **会**复制上下文（`current_user_id.get()` 在任务内能取到值），但 `src/api/chat.py:42-43` 的注释断言「任务与请求不共享 context」，与该用法**自相矛盾** —— 一旦有人按注释去「修正」或调整中间件顺序，`user_id` 会**静默变空**。做法与 `trace_id` 对齐：捕获 + 显式传参，并顺带订正那条误注释。（未登录时是 cookie 里的匿名 uuid，仍可用于「同一访客多轮」聚合。）
- `tags` 只放**低基数且稳定**的值：`chat` + （`kb` | `no_kb`）。**实测确认 tags 通过 API 只能并集合并、无法删除** —— 写错或改口径会永久留痕，故：agent / skill / kb_id 一律进 `metadata`（可改、无残留）。
- `metadata` = 生效智能体（`agent` / `agent_display_name`）、本轮技能（`skill_action` / `loaded_skills`）、`kb_id` / `kb_domain` / `deep_thinking` / `direct_skill` / `has_skills`。**只读 `RequestContext`**，不新增取数。

### D7 成本：配置字段默认 0，`0 = 未配置 → 跳过 seed`

- 单价进 `src/config/settings.py`（env 驱动，默认 0），与「硬编码集中管理」的既有约定一致。
- **0 视为未配置**：CLI 跳过 seed —— 避免在 Langfuse 里留下一个会被误读成「免费」的零价模型定义。填了真实单价再 seed。
- 落地走 repo 内**幂等** CLI（`langfuse_context.client_instance.api.models` 的 `list` + `create`；`list` **需翻页**遍历），dev / prod 各跑一次；比 UI 手配可复现，比 `cost_details` 保留模型定义页与成本分析能力。
- **模型定义的三个易错项写死**：
  - `model_name = qwen3.8-flash`
  - `match_pattern = (?i)^qwen3\.8\-flash$` —— 它是**正则**，必须锚定。写错（例如只写 `qwen3.8-flash` 而正则里 `.` 通配）或漏锚定会导致**匹配不到**，表现为「seed 成功但成本仍为 0」的**静默失败**。
  - `unit = TOKENS`
- **价格单位是 USD**（Langfuse 的 `input_price` / `output_price` 以美元计）。若要以人民币口径录入，必须在配置注释里写明换算关系，**不得**直接把人民币数值填进去（量级约差 7 倍）。
- 口径说明：`usage_estimated=true` 的轮次，其成本是「估算 token × 单价」，非真实账单。

### D8 MCP 预留：只留位置，不实现逻辑

三个可选 hook 现在只把「写死的那几行」抽成小函数，不实现内容：**入参归一化**（`_on_start` 前）、**输出摘要**（`_on_end` 前）、**来源标记**（span 的 `metadata.source`）。将来的取值来源（事件 `metadata` 里有没有 MCP 标识）留待 MCP 落地时确认。

### D9 边界：fork 子代理为何不覆盖，以及与既有 change 的关系

- **fork 子代理不覆盖的真实原因是「回调传播被切断」，不是「被 `scope` 过滤」**：`src/agents/skills/executor.py:189` 显式 `var_child_runnable_config.set(None)`（注释说明这是为了防 SSE token 污染），子代理事件因此**不进主图 `graph.astream_events`**；它们只走 `sub_agent.astream_events(...)`（`fork_stream.py:114`）并经 `clarify_channel` 转成 `delegate` 增量。`_convert_event` 里的 `scope != main` 只是对这些 **delegate dict** 的转换口径，与"过滤工具事件"无关 —— 设计文档与评审口径均按此订正。
- 与 `turn-provenance-observability`（0/36）：数据同源、函数重叠，**串行，不并行**；本变更在它之前。trace metadata 只读 `ctx`，将来它把 `_resolve_session_agent` 改成结构化返回时同步供 metadata 使用即可，不需要在 trace 侧再取一遍。
- 工具事实不做「一套事实三 sink」的重构：**工具日志留在工具内部**（`retrieval_signal(empty_result/reretrieve)`、`retrieve_done(result_count)` 依赖工具内部状态，事件流拿不到）。本变更只新增 Langfuse 这个 sink。

### D10 开工前待定项（定稿）

- **父 span 的 `name` 用 `tools`，不带轮次**：与 LangGraph 节点名一致，便于在 Langfuse 里按 `name` 聚合；轮次已由时间顺序与子 span 的父子关系体现，自造 `tools[step=N]` 只会引入一个无从对齐的命名空间。
- **seed CLI 的幂等判据按 `model_name` 已存在即跳过**：只比 `model_name`（Langfuse 侧同名即视为同一定义），不比 `match_pattern` —— 判据简单可预期，避免「改了 pattern 就多出一份定义」这类隐蔽行为。`match_pattern` 取值见 D7。
- **本期不把「工具返回条数」等摘要写进 span**：那需要从 output 文本反向解析，属 D8 的输出摘要 hook；本期 span 的 `output` 直接记录工具返回值原文。

## Risks / Trade-offs

- **命令式与装饰器混用**（flush / 禁用态 / 异常路径口径不一）→ 采集器自带 `_enabled` 守卫；client 统一取 `langfuse_context.client_instance`（D3）；`close()` 在 `finally`；禁用态整条路径短路。落地时用 `LANGFUSE_ENABLE=false` 回归一轮，确认零产出且对话行为不变。
- **过滤判据写错会导致「零工具 span」**（近 Blocker）→ 只用 `metadata.langgraph_node == "tools"`，**不得**用 `checkpoint_ns` 判空（D4）。E2E 必须断言「工具 span 数量 == 实际工具调用次数」，否则该错误会以「trace 里什么都没有」的形式静默通过。
- **定价 pattern 写错会导致「seed 成功但成本仍为 0」**（静默失败）→ `match_pattern` 锚定正则 + E2E 断言真命中（D7）。
- **`data.output` 是 `ToolMessage` 对象**，直接塞进 span 会序列化成不可读的东西 → `_on_end` 显式取 `.content` / `.tool_call_id` / `.name` 后再写。
- **取消 / 异常导致 span 悬空** → `close()` 兜底（与「进程退出前必须 flush」同类问题）。
- **trace `timestamp` 漂移** → 由 D3 根除（不调 `client.trace()`）。
- **`user_id` 静默变空** → 由 D6 根除（显式捕获传入 + 订正误注释 + 加断言）。
- **载荷体积增长**（工具返回原文进 span output，检索结果可达数 KB）→ 接受；与 `input` 已在承载同样内容的事实一致。若将来需要截断，落在 D8 的「输出摘要」hook。
- **子代理事件混入** → fork 子代理事件压根不进主事件流（Context 表 / D9）；其余按 `metadata.langgraph_node` 过滤（D4）。
- **验收只能在 dev 真跑**（Langfuse 服务端行为无法用单测替代）→ dev E2E 必过清单：一轮含 ≥3 迭代、≥2 次工具调用；**工具 span 数量 == 实际调用次数**；每轮 `tools` 父 span + 子 span；工具轮 `output` 非空；`user_id` / `tags` / `metadata` 落库；**seed 定价后 ≥1 条 generation 的 `totalCost > 0`**；取消路径无悬空 span。
- **「成本可见」不可证伪的风险** → 「seed 后 `totalCost > 0`」是**必过闸门**，不得降级为可选；否则验收可全绿而成本仍为 0。

## Migration Plan

1. 代码落地后在 `LANGFUSE_ENABLE=false` 下回归一轮（零产出、行为不变）。
2. dev 侧开启后跑 E2E 必过清单。
3. dev 侧填单价（USD 口径）并跑一次 seed CLI，确认 `totalCost > 0`。
4. 回滚策略：本变更为纯新增观测，回滚 = revert 提交；Langfuse 侧已写入的历史 trace 无需处理（保留期清理由既有 purge CLI 负责）。

## Open Questions

（无 —— 开工前待定的三条已定稿，见 D10。）
