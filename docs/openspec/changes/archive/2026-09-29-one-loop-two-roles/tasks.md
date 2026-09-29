## 0. 前置与排序

- [x] 0.1 **闸门已过（2026-09-28 核对）**：`skill-execution-and-delegation` 的 P1+P2 均已落地并归档（归档提交 `c6c8ea8`；合并提交 `95c81ff`），`executor.py` / `fork_stream.py` / `fork_tools.py` / `tool_trace.py` / `delegate_task.py` 的新形态已在库
- [x] 0.2 **开工基线复核**：确认对方已按其 D11 落地 `ToolTraceCollector` 的 `scope` / `parent_span` / `name_prefix` 三参数（缺省行为不变），且 `_convert_event` 的模型事件判别仍为 `"agent"`（本变更要改为 `"model"`）
- [x] 0.3 建立本变更的分支/worktree 并在计划顶部写明排序声明（本变更 after `skill-execution-and-delegation`，before `agent-round-budget`）
- [x] 0.4 记录当前基线：`POSTGRES_HOST=localhost pytest tests/ -v` 全绿、`ruff check .` 无错误、`pyright src/` 无新增 error（作为后续对比基线）
- [x] 0.5 `delegate-task` delta 已在其归档后 rebase（2026-09-28 完成，见该 delta 文件顶部说明）；归档时若该 requirement 又有新改动，须重做一次
- [x] 0.6 **改动前采基线快照**（必须在动代码前完成）：用固定问题集（绑 KB / 未绑 KB 各一）跑一次，记录五条日志（`iteration done` / `iteration limit` / `model turn` / `prompt assembled` / `prompt messages`）的**条数与字段值**、SSE 事件序列、`[n]` 与 citations，落 `docs/tmp/`（或测试夹具）。§7 的"逐字不变"断言以此为比对基准——无基线则"行为保持"不可证明

## 1. 装配入口与 middleware（新文件）

- [x] 1.1 新建 `src/agents/graph/agent_factory.py`：`build_agent(model, tools, system, max_turns, middleware_extra)` —— `system` 支持两形态（静态串 / 经运行态携带）；主/子角色共用；**不产出** `graph compiled` 日志；**不传 `name`**（角色名参数作为预留位保留但两角色都不启用——传了会给事件 metadata 加 `lc_agent_name`，属可观测元数据变更）
- [x] 1.2 同文件：定义子图 `state_schema`（`AgentState` 子类）——**所有自定义键必须在此声明**，否则静默丢弃。**键名在此钉死**：`_system_messages`（system 段，跨 invoke 由外层 state 提供）、`_turn_count`（回合计数）、`_delegate_used`（委派标志）、`kb_id`、`query`、`deep_thinking`。**两角色共用同一 schema**（未用者留默认），不得各建一套。⚠️ **schema 上的默认值不会被自动填充**：实测只传 `messages` 时，middleware 看到的键**只有 `messages`**，`state.get("kb_id")` 为缺失 ⇒ 凡消费者依赖的键都**必须由节点显式 seed**（见 2.2），不能靠默认值兜底
- [x] 1.3 新建 `src/agents/graph/middleware.py`：`SystemMessagesMiddleware` —— 从**子图 state** 的 `_system_messages` 读 system 段并施加（该值是节点从**外层** `AgentState` 的声明字段 seed 进来的；middleware 看不到外层 state，故 key 由节点显式传入）；**确切机制**：`request.override(system_message=<第一条>, messages=[<第二条>, *request.messages])`（模型节点把 `system_message` 置于最前，故实收 `[第一条, 第二条, ...]`，顺序与 `src/rag/prompt.py:248-250` 一致）；未绑 KB 时才有第二条；**必须同时实现同步与异步钩子**（只实现同步 `wrap_model_call` 时，异步上下文会抛 `NotImplementedError`）
- [x] 1.4 同文件：`ModelParamsMiddleware` —— **承接今天内联在调用点的参数施加**（`agent_node.py:203-218`），用 `request.override(model_settings=…)` 下发 `temperature` 与 `extra_body.enable_thinking`。**两个取值都必须来自 seeded 的图状态**（今天分别读外层 `state.kb_id` 与 `state.deep_thinking`）：① **档位判据** = `current_request_ctx.kb_bound`（`ctx is None` 时回退 `bool(state.get("kb_id"))`——**不是 `state.kb_id`**，middleware 的 state 是映射、属性访问抛 `AttributeError`）；② **思考开关** = seeded 的 `deep_thinking`（CLI 入口无 ctx，故以 seed 为准；**漏传即等于对所有请求关闭深度思考**）。KB 档用**不带 `temperature` 键**表达"不传"（不得传 `None`）；**把 `agent_node.py:187-189` 那条「per-call `extra_body` 整体覆盖构造时 `extra_body`，故 `LLM_KWARGS` 不宜再配 `extra_body`」的约束注释原样带过来**
- [x] 1.5 同文件：`AgentTurnBudget` —— `after_model` 判定（计数、委派检测、有效上限含 `MAX_DELEGATE_BONUS` 放宽）；**日志与 jump 解耦**：计数达到有效上限时**即**记 `iteration limit`（`query` / `iteration` 取自 state），**与"该轮是否仍声明工具调用"无关**；需要 jump 时返回 `{"jump_to": "end"}`（**不注入任何消息**）；**委派放宽须「本轮命中先置位 OR 此前已置位」两者兼有且先置位再算上限**；**不得**使用官方 `ModelCallLimitMiddleware`；**只装配给主角色**（子角色上限仍由 fork 消费侧判定）
- [x] 1.6 同文件：`AgentSpanMiddleware` —— **必须在 middleware 列表最内层**（这样它看到的是已被施加的 request，能直接读 `request.model_settings` 上报真实档位）；`awrap_model_call` **入口**产出 `iteration done` 日志（口径：`msgs = len(request.messages) + 1`、`iteration = state.get("_turn_count", 0) + 1`）、**出口**产出 `agent_turn` generation observation 与 `model turn` 日志；沿用今天 `capture_input=False` / `capture_output=False`（阻止 SDK 自动捕获把节点返回的 state dict 写进 trace），观测的 `input` / `output` 仍显式设置为完整消息列表与模型输出，`completion_start_time` 取首 chunk 到达时刻；观测故障须吞异常降级，不得影响对话
- [x] 1.7 **middleware 顺序钉死**：`[SystemMessagesMiddleware, ModelParamsMiddleware, AgentTurnBudget, AgentSpanMiddleware]`（前者在外层；`AgentSpanMiddleware` 必须在最内层）
- [x] 1.8 四个 middleware 类**均不得**在实例属性上保存 per-request 状态（实例随图构造一次、跨请求共享；计数/本轮参数一律走 state 或请求上下文）；**读图状态一律用 `state.get(...)`**（实测 middleware 收到的是**映射**，`state.kb_id` 抛 `AttributeError`），且读之前先确认该键**已由节点 seed**（未 seed 的键不存在，不是"取默认值"）

## 2. 主图接入与工具取数形状

- [x] 2.1 `src/agents/graph/agent_node.py` — `_initial_messages` 改为"产两半"：调 `build_prompt(...)` 后按类型拆分——**system 段写入外层 `AgentState` 的声明字段**（每个生成只组装一次），非 system 段 → 子图输入；保留 `_truncate_history` / `SKILL_INJECTION_PREFIX` 抽取 / `clean_prefix` / `user_template` 四步原样。⚠️ **`prompt messages` 的 `system_msgs` 计数必须在拆分之前算出**（今天它从消息列表里数 `SystemMessage`，`agent_node.py:139-142`；拆分后列表里没有 system 段 ⇒ 计数会变 0），`injected_msgs` / `history_msgs` 口径不变
- [x] 2.2 同文件 — 新增 `make_agent_loop_node(bundle)`：`state.messages` 为空时组装首轮；**非空时（含重生成轮）不再组装，但 `_system_messages` 仍从外层声明字段取并传给子图**；构造子图输入时**必须 seed 全部消费者依赖的键**（未 seed 的键在子图状态里**不存在**，不是取默认值）：`_system_messages`（外层声明字段）、`query`（外层 `state.query`）、**`kb_id`（外层 `state.kb_id`）**、**`deep_thinking`（外层 `state.deep_thinking`）**、`_turn_count` / `_delegate_used`（**字面初值 `0` / `False`**——其外层来源字段本变更已删，这正是"regen 轮预算天然复位"的实现点）。⚠️ `kb_id` / `deep_thinking` 在 `RequestContext` 里虽有对应项，但**CLI 评估入口不建 ctx**（`kb_id` 只放进图输入）⇒ 不得依赖 ctx 回退。`invoke` 子图后**按外层已有条数回写**：`{"messages": produced[len(state.messages):], "answer": <末条文本>}`——**基准是外层 `state.messages` 的长度，不是喂给子图的输入长度**（首轮外层为空 ⇒ 回写整份组装段 + 新增段，复现今天 `[*messages, result]` 的行为；否则 regen 轮会丢问题与历史，见 design D7）
- [x] 2.3 同文件 — **删除** `make_agent_model_node` / `make_agent_tools_node` / `route_agent`；保留 `make_agent_finalize_node` 原样
- [x] 2.4 `src/agents/graph/workflow.py` — 三件结构变更：① `add_node("agent", make_agent_loop_node(bundle))`（节点名保持 `"agent"`，`route_verify` 回边目标名不变）；② **删除外层 `tools` 节点**（`add_node("tools", …)`）**与 `add_edge("tools", "agent")`**——循环已内化进装配产物；③ `agent` 的出边由 `add_conditional_edges("agent", route_agent, …)` 改为**直连 `agent_finalize`**。`tool_sink` 供给链、`delegate_task` / `skill_direct_node` 注入面保持不变；`graph compiled` 仍在此处发一次。**补一句代码注释**：`ToolTraceCollector` 的 `langgraph_node == "tools"` 判据仍有效（工具事件来自子图内同名节点，不是外层图），防止后人误判失效而改坏
- [x] 2.5 确认 `agent_node.py` 行数回落至 400 行以内（红线）
- [x] 2.6 **删除** `agent_node.py` 内的临时取证埋点：`first_chunk_ms` 与 `logger.info("[agent] TIMING model_turn …")`（`:282-284`）随迁移删除；`first_chunk_at`**保留**（它是观测的 `completion_start_time`，属功能字段）
- [x] 2.7 `src/agents/tools/rag_tools.py` — **`kb_id` 与形状守卫已由 `skill-execution-and-delegation` 落地**（其提交 `8014d64`：`isinstance(state, AgentState)` 优先 + `ctx.kb_id` 回退）。本变更只需补一处：**迭代序号**。A 之后主循环的注入状态是 dict，其现有守卫会让 `iteration` 恒取 **0** ⇒ 改为「dict 状态下读子图 schema 的 `_turn_count`（键名见 1.2）」或等价显式判定，使主循环内工具仍上报真实序号
- [x] 2.8 `src/agents/tools/ask_tools.py` — **3 处取数需修**（`:84` `state.query` / `:85` `state._agent_iterations`、`:168-169` `state.kb_id`）：改显式形状判定（`isinstance` 分流；`kb_id` 按既有约定走 `ctx` 回退）。⚠️ 对方**有意未修**此处并留了注释「若将来把它移出禁用集，须同 retrieve_kb 一样加 `isinstance(state, AgentState)` 守卫」——其判断在**它的范围内成立**（`ask_user` 在 fork 禁用集里，子代理调不到），但**主循环改由 `create_agent` 承载后 `ask_user` 就在 dict 状态下被调用** ⇒ 本变更必须处理
- [x] 2.9 两文件的方法 docstring 写明：注入状态的形状由**承载它的图实现**决定；取数通道**逐个字段定**——`kb_id` 走「注入优先 + `ctx` 回退」（装配已 seed，回退只作补充）；`query` / 迭代序号**只**在图状态里可得；`kb_id` / `deep_thinking` 在 `RequestContext` 里虽有对应项，但**存在不建 ctx 的入口（CLI）**，故不得把 ctx 当"总是有"
- [x] 2.10 降级分支**留痕**：字段缺失走降级时记 warning（含工具名与缺失字段名），不得静默取空

## 3. SSE 判别谓词

- [x] 3.1 `src/services/agent_service.py` — `_convert_event` 里模型事件的判别由 `langgraph_node == "agent"` 改为 `== "model"`（`on_chat_model_start/stream/end` 三处，`:250/267/277`）；判据**实际只有「节点判别键」一维**（`scope` 形参虽在、但生产调用点全用缺省 `main`，见 3.4），**不得**引入 `checkpoint_ns` 前缀等更宽的匹配面
- [x] 3.2 同文件 — 更正模块 docstring（`:186-208`）里残留的 `"agent"` 描述
- [x] 3.3 同文件 — 确认域节点判别（`format` / `agent_finalize` / `skill_direct` 的 `on_chain_end` 名字过滤）与 `ToolTraceCollector` 的 `== "tools"` **未改动**
- [x] 3.4 确认 `_convert_event` 的 `scope` 参数**当前不参与判别**（三处调用 `agent_service.py:437/471/703` **均用缺省 `main`**，函数 docstring `:184-188` 亦自陈"当前实现下 graph 事件仅在 `scope=="main"` 时转换"）。本变更**不改它**，但 SHALL NOT 在代码注释/文档里把它当作"已生效的第二道防线"；并在该函数处写明「主 SSE 与子代理事件的隔离由**事件路由**承担（切断回调继承 + 委派事件经显式喂事件走委派域），属**单点机制**」
- [x] 3.5 **同步测试侧的谓词副本与事件夹具**（易漏项）——按实测清单**逐文件核对**，共 **5 个文件**用 `metadata: {"langgraph_node": "agent"}` 造模型事件：`tests/services/test_agent_service.py`、`tests/services/test_dual_stream.py`、`tests/agents/skills/test_skill_executor.py`、`tests/agents/skills/test_delegate_task.py`、`tests/infra/llm/test_tool_trace.py`（**最后一个是否定用例**——它验的是 `tools` 判据不误收，改值只影响叙述、不影响语义，须显式判断而非机械替换）；另 `test_skill_executor.py:586-620` 有一份**注释自称"与 `agent_service._convert_event` 同口径"的谓词副本**，须同步为 `"model"` 并更新注释。只改 `src/` 会让这些夹具/副本继续绿却已与现实脱节

## 4. 状态字段清理与 regen 复位删除

- [x] 4.1 `src/agents/graph/state.py` — 删除 `_agent_iterations` / `_max_agent_iterations` / `_delegate_used` 三个字段；`max_agent_iterations` 的语义迁入装配参数（**主角色**）；**同批新增 `_system_messages` 声明字段**承载 system 段——类型 `list[SystemMessage]`、**不带 reducer**（写入即替换，不参与 `add_messages` 的追加/去重语义）、跨 invoke 持久，供 §2.2 的重生成轮与 §1.3 的 middleware 读取（外层未声明的键会被 LangGraph 静默丢弃）
- [x] 4.2 **三处** regen 预算复位统一处置：`src/agents/graph/verify/guardrails.py:105/106`、`:180/181` **与** `src/agents/graph/verify/regen_decision.py:174/175` —— 删除 `"_agent_iterations": 0` 与 `"_delegate_used": False`；`ctx.web_count = 0`（**两处**：`guardrails.py:99`、`regen_decision.py:169`）与主循环预算无关，**保留**
- [x] 4.3 在代码注释中写明：regen 轮主循环预算独立起算，由"每次 invoke 即新 run"承担（不再靠显式复位）
- [x] 4.4 同步 `tests/agents/graph/test_verify_node.py` 里多处直接断言这些返回键的用例（`:300/439/481/537/643/695/727-739` 等）
- [x] 4.5 复核死字段登记——**已核实为四个**（`timings` / `_token_usage` / `model_used` / `is_fallback`，后两个同样零读写；`agent_service.py:763` 的 `is_fallback` 硬编码 `False` 属既有失真，单列一条）；四个**都不在本变更清理**，只登记（`requirements_pool` D-09 须同步修正为四个）

## 5. 子角色接入

- [x] 5.1 `src/agents/skills/executor.py` — `_build_sub_agent` 改调装配入口（**只传**：模型、工具面、静态 system 串），删除自行拼接 `create_agent` 的代码
- [x] 5.2 子角色的 system 保持静态串（执行者人设 + `FORK_EXECUTION_CONTRACT`），`middleware_extra` 传**空列表**（不装配 `SystemMessagesMiddleware` / `ModelParamsMiddleware` / `AgentTurnBudget` / `AgentSpanMiddleware`）——子代理的模型轮次记录由 fork 消费侧 `_record_delegate_model_turn` 承担，图内不得重复产出主循环口径的轮次日志与观测
- [x] 5.3 **不传** `max_turns`（子角色的轮次上限仍由 fork 消费侧判定，见 `agent-assembly` 的适用范围）；**不传** `name`（今天也没传，`executor.py:320` 无该参数）
- [x] 5.4 确认 `_run_fork` 的 SSE 隔离（`var_child_runnable_config.set(None)`）与委派域采集器喂事件**均未被本变更改动**

## 6. 观测与日志字段逐字复现

- [x] 6.1 `model turn` — 字段集 `(model, usage_in, usage_out, usage_estimated, fallback, latency_ms, iteration, temperature, temp_source, kb_bound)` 逐字保持；`kb_bound` **与档位判据同源**（读 `current_request_ctx.kb_bound`，见 1.4）；`iteration` = `state.get("_turn_count", 0) + 1`（本次调用序号）；`temperature` / `temp_source` 直接取**实际施加**的档位（D13）
- [x] 6.2 `iteration done` — 字段集 `(iteration, msgs)` 逐字保持；`iteration` = `state.get("_turn_count", 0) + 1`；`msgs = len(request.messages) + 1`（含 system 段的完整条数，口径见 D13/1.6）；产点为 `AgentSpanMiddleware.awrap_model_call` 入口（与今天"模型调用前"一致）
- [x] 6.3 `iteration limit` — 字段集 `(query, iteration)` 逐字保持；`query` 经运行态 schema 携带；`iteration` 用 `after_model` 内自增后的 `n`（与判定用的有效上限同源）
- [x] 6.4 `prompt assembled` / `prompt messages` — 确认产点**未变**（组装仍在 `build_system_prompt` / 节点路径）；三个计数（`system_msgs` / `injected_msgs` / `history_msgs`）**逐字不变**，其中 `system_msgs` 必须在**拆分之前**算出（见 2.1）
- [x] 6.5 温度分档：由 `ModelParamsMiddleware` 施加；**判据取 `current_request_ctx.kb_bound`**（`ctx is None` 时回退 **`bool(state.get("kb_id"))`**——该键已由 2.2 seed）；思考开关取 **seeded 的 `deep_thinking`**；KB 档用**不带 `temperature` 键**表达"不传"（不得传 `None`）；非 KB 档显式传配置值；`extra_body` 的浅合并约束注释原样带过来（见 1.4）
- [x] 6.6 新增/变更的事件若涉及 `log_events.py` / `log_event_specs.py` 登记，两处同名且字段集与实参严格一致（import 期硬断言）

## 7. 测试

- [x] 7.1 `tests/agents/graph/test_agent_node.py` / `test_graph.py` — 按新节点形态与装配入口改造；`test_graph.py` 里 `make_rag_tools` 的 mock 面随 `build_graph` 调整
- [x] 7.2 `tests/agents/graph/test_state.py` — 三个字段删除后的断言更新
- [x] 7.3 **新增**：触顶语义断言 —— 模型调用 = 上限次、工具执行 = 上限−1 次、末条为含 `tool_calls` 的 `AIMessage`、`answer` 可为空串（对应 `agent-loop-observability` 的新 requirement）
- [x] 7.4 **新增**：middleware `state_schema` 断言 —— 计数/携带键均已在 schema 声明（改动计数字段名时测试必须失败）
- [x] 7.5 **新增**：跨请求隔离 —— 两个并发使用同一 middleware 实例，各自计数/参数互不污染
- [x] 7.6 **新增**：regen 轮预算独立起算 —— 首轮耗满上限后，重生成轮的工具调用完整执行
- [x] 7.7 **新增**：system 施加逐字一致 —— 未绑 KB 时模型实收两条 system 消息且内容与 `build_system_prompt` 输出逐字一致；一次生成内 `prompt assembled` 只产出一条；**`prompt messages` 的三个计数与基线一致（`system_msgs` 不得为 0）**；观测的 `input` / `output` 仍为显式设置的完整消息列表与模型输出（`capture_input` / `capture_output` 仍为 `False`）
- [x] 7.8 **新增**：装配入口不产出 `graph compiled` —— 委派一次后该事件计数不变
- [x] 7.9 `tests/services/test_run_generation_tracing.py` / `test_agent_service.py` / `test_dual_stream.py` — SSE 事件序列与 token 流不回归
- [x] 7.10 `tests/infra/llm/test_tool_trace.py` — 主图工具 span 结构不回归（应为零改动）
- [x] 7.11 `tests/agents/graph/test_direct_skill_round.py` / `test_verify_node.py` / `test_verify_material_source.py` — 直出轮与 verify 材料来源不回归
- [x] 7.12 `tests/agents/skills/test_skill_executor.py` / `test_delegate_task.py` — 子角色改走装配入口后的装配断言
- [x] 7.13 **新增**：`create_agent` 承载下工具取数正确 —— ①在 `create_agent` 图内调用 `ask_user`，断言注入状态为 `dict` 时仍取到 `query` / `kb_id` 且不抛 `AttributeError`；②`retrieve_kb` 的**迭代序号**在主循环（dict 状态）下为真实值、**不为 0**；③降级分支产出 warning（含工具名与缺失字段名）
- [x] 7.14 **新增**：重生成轮的上下文不丢 —— 首轮组装后经 `verify` 触发重生成，断言重生成轮的**模型请求同时含**：① 完整 system 段（含未绑 KB 的第二条）、② **原始 query 与历史**（即首轮组装出的非 system 段已落回外层 state）；且 `prompt assembled` 只产出一条。⚠️ 基准取错的症状是"regen 轮模型看不到问题"，只测 system 段会漏掉它
- [x] 7.15 **新增**：五条日志的**产出次数与字段值** —— 一次生成内 `iteration done` / `iteration limit` / `model turn` / `prompt messages` / `prompt assembled` 的条数**与字段值**均须与 §0.6 基线逐字一致（只对条数不够——`msgs` / `iteration` / 三个计数都属易漂字段）；含"上限轮恰好正常收尾也产出 `iteration limit`"这一情形
- [x] 7.16 **新增**：取消/断连穿过"节点内 invoke 子图" —— 置位 `abort_signal` 后断言主任务收到 `CancelledError`、子图不遗留悬挂、`tool_trace.close()` 被调（评审 Important；须在**单元层**覆盖，不能只靠 E2E）
- [x] 7.17 **新增**：委派放宽的两个方向 —— ①上限同轮声明 `delegate_task` 时该工具**被执行**（不被跳过）；②第 N 轮委派后第 N+1 轮未声明委派，有效上限**不回落**
- [x] 7.18 **新增**：`capture.model_used` 捕获 —— 判别谓词改 `"model"` 后，`on_chat_model_end` 仍能把 `model_used` 写进 `capture`（`test_run_generation_tracing.py` 里点名断言，不只写"不回归"）
- [x] 7.19 **新增**：生产链路端到端携带字段 —— 走"生产节点 → 子图 → 工具"的完整路径断言 `retrieve_kb` 取到**真实** `kb_id`、`ask_user` 取到**真实** `query`（SHALL NOT 用测试里手工塞入的 state 替代、也 SHALL NOT 落在降级分支）——这是形状修复的**残留风险**守卫
- [x] 7.20 **新增**：温度分档取值 —— 绑 KB 与未绑 KB 各一条，断言传给模型的 kwargs 中温度档位正确（未绑档显式传配置值、绑 KB 档**不带 `temperature` 键**），并断言 `model turn` 日志的 `temperature` / `temp_source` / `kb_bound` 与之一致。⚠️ **必须走完整生产链路**（生产节点 → 子图 → middleware → 模型），SHALL NOT 用测试里手工塞入的 `kb_id` 掩盖判据错误——D15 的风险正是在"state 里没有 kb_id"时暴露
- [x] 7.21 **新增**：静态扫描断言「唯一装配」—— `src/agents/` 下对 `create_agent(` 的调用只允许出现在装配入口文件（`agent_factory.py`）；出现第二处即失败
- [x] 7.22 **新增**：基线逐字比对 —— 用 §0.6 的固定问题集重跑，`assert` 五条日志的条数+字段值、SSE 事件序列、`[n]` 与 citations 与基线一致（这是"行为保持"的总闸门）
- [x] 7.23 **新增**：子角色观测不重复 —— 一次委派后，主循环口径的 `model turn` / `iteration done` 条数**不因委派而增加**（子代理的轮次记录只走 `delegate model turn`）
- [x] 7.24 **新增**：fork 事件不泄漏进主 SSE —— 一次生成内发生 fork 委派（含 `/xxx` 直出轮），断言子代理的模型增量**不出现在主答案的 token 流与完整答案累积**中（改谓词后失去"节点名偶然挡住"的第二层，隔离只剩事件路由单点，须有守护断言）
- [x] 7.25 **新增**：无 ctx 入口的字段可得性 —— 以 **CLI 生产方式**（`graph.ainvoke({"kb_id": …, "query": …, …})`，**不 set `RequestContext`**）跑一次，断言图内 `retrieve_kb` 取到真实 `kb_id`、`ModelParamsMiddleware` 的档位判据为"已绑 KB"（`model turn` 的 `kb_bound=True` 且**不带** `temperature`）。这是"`kb_id` 必须 seed"的守卫（`src/cli/check_abstain.py:127` / `eval_ragas.py:129` 的真实形态）
- [x] 7.26 **新增**：思考开关随调用链传入 —— 会话开关开启时，断言施加给模型的 `extra_body.enable_thinking` 为真（且 `state.deep_thinking` 已随子图输入传入）；未开启时为假。这是"`deep_thinking` 必须 seed"的守卫

## 8. 文档

- [x] 8.1 `docs/agents/code-map.md` — `graph/` 结构（新增 `agent_factory.py` / `middleware.py`）与 `agent_node` 职责行
- [x] 8.2 `docs/agents/data-flow.md` — 循环图、节点职责表、首轮消息组装路径
- [x] 8.3 `docs/agents/api_contract.md` — `build_graph` 契约与新的装配入口签名
- [x] 8.4 `docs/agents/glossary.md` — 新术语（装配入口 / 回合预算 middleware / system 提供方式 / 循环后阶段）
- [x] 8.5 `docs/agents/prompt-ownership.md` — 补「system 段的施加通道」一句（组装仍在 `build_system_prompt`，下发经 middleware）
- [x] 8.6 `docs/agents/defensive-patterns.md` — **已在本变更立项时登记**两条（「自定义 middleware 必须声明 `state_schema`，否则自定义键被静默丢弃」与「middleware 实例跨请求共享，per-request 状态不得存在实例属性上」）；实现期复核**是否再补一条**：`extra_body` 的 per-call 整体覆盖（在 `LLM_KWARGS` 里配 `extra_body` 会**静默丢失**）
- [x] 8.7 `docs/agents/requirements_pool.md` — **已登记** D-09 与 F-37；实现期复核并**补登**：① 死字段由两个修正为**四个**（补 `model_used` / `is_fallback`；后者的硬编码 `False` 单列一条失真项）；② 临时取证埋点清理待办（`agent_node` 的 TIMING 随本变更删除；另三组 `web_tools` / `tavily_client` / `agent_service` 静默看门狗**不在本变更范围**）；③ F-36 的收窄后状态
- [x] 8.8 `docs/agents/logging-rules.md` — 若事件**产点**迁移需注明（事件名与字段不变）
- [x] 8.9 撰写 ADR（一条：统一 agent 循环到 `create_agent`）；编号按 `dev-wsl` **当时**最大号 +1 取（当前最大 `0015` ⇒ `0016`），取号前与当时的在途变更协调

## 9. 验收

- [x] 9.1 `POSTGRES_HOST=localhost pytest tests/ -v` 全绿；`ruff format . && ruff check .` 无错误；`pyright src/` 不新增 error
- [x] 9.2 无遗留 `print()` / TODO / 调试代码；`agent_node.py` < 400 行；**`agent_node.py` 内的 `TIMING` 临时埋点已删净**（`grep TIMING src/agents/graph/` 应为空）
- [x] 9.3 真实模型 E2E（DashScope，绑 KB 与未绑 KB 各一次）：token 级流式内容正确、`[n]` 引用与 citations 一致、`extra_body`/思考档生效、**绑 KB 轮未出现显式 `temperature` 参数**（D15 的线级验证）
- [x] 9.4 真实模型 E2E：`/xxx` 直出轮与主 agent 委派轮各一次，确认子代理链路未回归（含委派折叠区事件）
- [x] 9.5 触顶路径真实复现：宽泛问题触发触顶，日志含 `iteration limit`，**不新增**提示文案，`verify` 收到空答案后照常收尾（与变更前行为一致）
- [x] 9.6 `agent_turn` generation observation 在 Langfuse 上仍出现且字段完整（迁 middleware 的验证）；失败则回退为命令式 span 并记录
- [x] 9.7 `delegate-task` delta 的 rebase **已兑现**（2026-09-28）；归档时若该 requirement 又有新改动，**须**重做一次复制+施加（否则会回退其工具面改动）
- [x] 9.8 `openspec validate one-loop-two-roles` 通过；ADR 通过 `src/cli/check_adr.py`
- [x] 9.9 **F-36 的收窄后登记已核对**：对方在 `skill-execution-and-delegation` 落地时**自己发现并修了 `retrieve_kb`**（`8014d64`）⇒ 原计划的"紧急同步"不再必要；剩余（`ask_user` 3 处 + `query`/迭代序号的装配携带）由本变更承接，已在 `requirements_pool` 更新
