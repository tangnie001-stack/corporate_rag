## 0. 前置与排序

- [ ] 0.1 **闸门已过（2026-09-28 核对）**：`skill-execution-and-delegation` 的 P1+P2 均已落地并归档（归档提交 `c6c8ea8`；合并提交 `95c81ff`），`executor.py` / `fork_stream.py` / `fork_tools.py` / `tool_trace.py` / `delegate_task.py` 的新形态已在库
- [ ] 0.2 **开工基线复核**：确认对方已按其 D11 落地 `ToolTraceCollector` 的 `scope` / `parent_span` / `name_prefix` 三参数（缺省行为不变），且 `_convert_event` 的模型事件判别仍为 `"agent"`（本变更要改为 `"model"`）
- [ ] 0.3 建立本变更的分支/worktree 并在计划顶部写明排序声明（本变更 after `skill-execution-and-delegation`，before `agent-round-budget`）
- [ ] 0.4 记录当前基线：`POSTGRES_HOST=localhost pytest tests/ -v` 全绿、`ruff check .` 无错误、`pyright src/` 无新增 error（作为后续对比基线）
- [ ] 0.5 `delegate-task` delta 已在其归档后 rebase（2026-09-28 完成，见该 delta 文件顶部说明）

## 1. 装配入口与 middleware（新文件）

- [ ] 1.1 新建 `src/agents/graph/agent_factory.py`：`build_agent(model, tools, system, name, max_turns, middleware_extra)` —— `system` 支持两形态（静态串 / 经运行态携带）；主/子角色共用；**不产出** `graph compiled` 日志
- [ ] 1.2 同文件：定义子图 `state_schema`（`AgentState` 子类）——**所有自定义键必须在此声明**，否则静默丢弃。**键名在此钉死**：`_system_messages`（system 段，跨 invoke 由外层 state 提供）、`_turn_count`（回合计数）、`_delegate_used`（委派标志）；**并且必须含 `kb_id` 与 `query`**——`retrieve_kb` 读 `state.kb_id`、`ask_user` 读 `state.query`/`state.kb_id`，缺这两键即"显式降级"成检索恒空（与形状不兼容同样危险）
- [ ] 1.3 新建 `src/agents/graph/middleware.py`：`SystemMessagesMiddleware` —— 从**外层 `AgentState` 的 `_system_messages` 字段**（跨 invoke 持久，见 4.1）读 system 段并施加；**确切机制**：`request.override(system_message=<第一条>, messages=[<第二条>, *request.messages])`（模型节点把 `system_message` 置于最前，故实收 `[第一条, 第二条, ...]`，顺序与 `src/rag/prompt.py:248-250` 一致）；未绑 KB 时才有第二条；**必须同时实现同步与异步钩子**（只实现同步 `wrap_model_call` 时，异步上下文会抛 `NotImplementedError`）
- [ ] 1.4 同文件：`AgentTurnBudget` —— `after_model` 判定（计数、委派检测、有效上限含 `MAX_DELEGATE_BONUS` 放宽）；**日志与 jump 解耦**：计数达到有效上限时**即**记 `iteration limit`（`query` / `iteration` 取自 state），**与"该轮是否仍声明工具调用"无关**；需要 jump 时返回 `{"jump_to": "end"}`（**不注入任何消息**）；**委派放宽须「本轮命中先置位 OR 此前已置位」两者兼有且先置位再算上限**；**不得**使用官方 `ModelCallLimitMiddleware`
- [ ] 1.5 同文件：`AgentSpanMiddleware` —— `awrap_model_call` 内产出 `agent_turn` generation observation（模型名 / usage / `completion_start_time` / `metadata`），字段与今天 `agent_node.py:249-268` 一致；记录 `model turn` 日志（字段集见 §6）
- [ ] 1.6 三个 middleware 类**均不得**在实例属性上保存 per-request 状态（计数/本轮参数一律走 state 或请求上下文）

## 2. 主图接入与工具取数形状

- [ ] 2.1 `src/agents/graph/agent_node.py` — `_initial_messages` 改为"产两半"：调 `build_prompt(...)` 后按类型拆分——**system 段写入外层 `AgentState` 的声明字段**（每个生成只组装一次），非 system 段 → 子图输入；保留 `_truncate_history` / `SKILL_INJECTION_PREFIX` 抽取 / `clean_prefix` / `user_template` 四步原样
- [ ] 2.2 同文件 — 新增 `make_agent_loop_node(bundle)`：`state.messages` 为空时组装首轮；**非空时（含重生成轮）不再组装，但 `_system_messages` 仍从外层声明字段取并传给子图**；构造子图输入时**必须一并 seed `kb_id` / `query` / `_turn_count` / `_delegate_used`**（工具依赖前两者取数、预算 middleware 依赖后两者计数，见 1.2）；`invoke` 子图后**按 `suffix` 回写**（只回写 seed 之后的新增段）+ 回写 `answer`（末条文本）
- [ ] 2.3 同文件 — **删除** `make_agent_model_node` / `make_agent_tools_node` / `route_agent`；保留 `make_agent_finalize_node` 原样
- [ ] 2.4 `src/agents/graph/workflow.py` — `add_node("agent", make_agent_loop_node(bundle))`（节点名保持 `"agent"`，`route_verify` 回边不变）；`tool_sink` 供给链、`delegate_task` / `skill_direct_node` 注入面保持不变；`graph compiled` 仍在此处发一次
- [ ] 2.5 确认 `agent_node.py` 行数回落至 400 行以内（红线）
- [ ] 2.6 `src/agents/tools/rag_tools.py` — **`kb_id` 与形状守卫已由 `skill-execution-and-delegation` 落地**（其提交 `8014d64`：`isinstance(state, AgentState)` 优先 + `ctx.kb_id` 回退）。本变更只需补一处：**迭代序号**。A 之后主循环的注入状态是 dict，其现有守卫会让 `iteration` 恒取 **0** ⇒ 改为「dict 状态下读子图 schema 的 `_turn_count`（键名见 1.2）」或等价显式判定，使主循环内工具仍上报真实序号
- [ ] 2.7 `src/agents/tools/ask_tools.py` — **3 处取数需修**（`:84` `state.query` / `:85` `state._agent_iterations`、`:168-169` `state.kb_id`）：改显式形状判定（`isinstance` 分流；`kb_id` 按既有约定走 `ctx` 回退）。⚠️ 对方**有意未修**此处并留了注释「若将来把它移出禁用集，须同 retrieve_kb 一样加 `isinstance(state, AgentState)` 守卫」——其判断在**它的范围内成立**（`ask_user` 在 fork 禁用集里，子代理调不到），但**主循环改由 `create_agent` 承载后 `ask_user` 就在 dict 状态下被调用** ⇒ 本变更必须处理
- [ ] 2.8 两文件的方法 docstring 写明：注入状态的形状由**承载它的图实现**决定；并**沿用对方的既有口径**（上下文可得字段走 `ctx` 回退，不重复 seed 进图状态）

## 3. SSE 判别谓词

- [ ] 3.1 `src/services/agent_service.py` — `_convert_event` 里模型事件的判别由 `langgraph_node == "agent"` 改为 `== "model"`（`on_chat_model_start/stream/end` 三处，`:250/267/277`）；判据**只由「节点判别键 + `scope == "main"`」两维组成**，**不得**引入 `checkpoint_ns` 前缀等更宽的匹配面
- [ ] 3.2 同文件 — 更正模块 docstring（`:186-208`）里残留的 `"agent"` 描述
- [ ] 3.3 同文件 — 确认域节点判别（`format` / `agent_finalize` / `skill_direct` 的 `on_chain_end` 名字过滤）与 `ToolTraceCollector` 的 `== "tools"` **未改动**
- [ ] 3.4 确认 `_convert_event` 的 `scope != "main"` 早退逻辑未受影响

## 4. 状态字段清理与 regen 复位删除

- [ ] 4.1 `src/agents/graph/state.py` — 删除 `_agent_iterations` / `_max_agent_iterations` / `_delegate_used` 三个字段；`max_agent_iterations` 的语义迁入装配参数；**同批新增 `_system_messages` 声明字段**承载 system 段（跨 invoke 持久，供 §2.2 的重生成轮与 §1.3 的 middleware 读取——外层未声明的键会被 LangGraph 静默丢弃）
- [ ] 4.2 **三处** regen 预算复位统一处置：`src/agents/graph/verify/guardrails.py:105/106`、`:180/181` **与** `src/agents/graph/verify/regen_decision.py:174/175` —— 删除 `"_agent_iterations": 0` 与 `"_delegate_used": False`；`ctx.web_count = 0`（**两处**：`guardrails.py:99`、`regen_decision.py:169`）与主循环预算无关，**保留**
- [ ] 4.3 在代码注释中写明：regen 轮主循环预算独立起算，由"每次 invoke 即新 run"承担（不再靠显式复位）
- [ ] 4.4 同步 `tests/agents/graph/test_verify_node.py` 里多处直接断言这些返回键的用例（`:300/439/481/537/643/695/727-739` 等）
- [ ] 4.5 复核两个已核实死字段 `timings` / `_token_usage` 的登记（已登记 `requirements_pool` D-09）

## 5. 子角色接入（依赖 §0.2）

- [ ] 5.1 `src/agents/skills/executor.py` — `_build_sub_agent` 改调装配入口（静态 system 串 + 工具面 + 上限 + 子角色 middleware 集合），删除自行拼接 `create_agent` 的代码
- [ ] 5.2 子角色的 system 保持静态串（执行者人设 + `FORK_EXECUTION_CONTRACT`），**不装配** `SystemMessagesMiddleware`
- [ ] 5.3 确认子代理的 `name` 参数取值与 `skill-execution-and-delegation` 的结论一致（定点取 skill 名；通用委派以其 D6 为准）
- [ ] 5.4 确认 `_run_fork` 的 SSE 隔离（`var_child_runnable_config.set(None)`）与委派域采集器喂事件**均未被本变更改动**

## 6. 观测与日志字段逐字复现

- [ ] 6.1 `model turn` — 字段集 `(model, usage_in, usage_out, usage_estimated, fallback, latency_ms, iteration, temperature, temp_source, kb_bound)` 逐字保持；`kb_bound` 读请求上下文
- [ ] 6.2 `iteration done` — 字段集 `(iteration, msgs)` 逐字保持
- [ ] 6.3 `iteration limit` — 字段集 `(query, iteration)` 逐字保持；`query` 经运行态 schema 携带
- [ ] 6.4 `prompt assembled` / `prompt messages` — 确认产点与计数**未变**（组装仍在 `build_system_prompt` / 节点路径；`system_msgs` 为 1 或 2）
- [ ] 6.5 温度分档：KB 档用**不带 `temperature` 键**表达"不传"（不得传 `None`）；非 KB 档显式传配置值
- [ ] 6.6 新增/变更的事件若涉及 `log_events.py` / `log_event_specs.py` 登记，两处同名且字段集与实参严格一致（import 期硬断言）

## 7. 测试

- [ ] 7.1 `tests/agents/graph/test_agent_node.py` / `test_graph.py` — 按新节点形态与装配入口改造；`test_graph.py` 里 `make_rag_tools` 的 mock 面随 `build_graph` 调整
- [ ] 7.2 `tests/agents/graph/test_state.py` — 三个字段删除后的断言更新
- [ ] 7.3 **新增**：触顶语义断言 —— 模型调用 = 上限次、工具执行 = 上限−1 次、末条为含 `tool_calls` 的 `AIMessage`、`answer` 可为空串（对应 `agent-loop-observability` 的新 requirement）
- [ ] 7.4 **新增**：middleware `state_schema` 断言 —— 计数/携带键均已在 schema 声明（改动计数字段名时测试必须失败）
- [ ] 7.5 **新增**：跨请求隔离 —— 两个并发使用同一 middleware 实例，各自计数/参数互不污染
- [ ] 7.6 **新增**：regen 轮预算独立起算 —— 首轮耗满上限后，重生成轮的工具调用完整执行
- [ ] 7.7 **新增**：system 施加逐字一致 —— 未绑 KB 时模型实收两条 system 消息且内容与 `build_system_prompt` 输出逐字一致；一次生成内 `prompt assembled` 只产出一条
- [ ] 7.8 **新增**：装配入口不产出 `graph compiled` —— 委派一次后该事件计数不变
- [ ] 7.9 `tests/services/test_run_generation_tracing.py` / `test_agent_service.py` / `test_dual_stream.py` — SSE 事件序列与 token 流不回归
- [ ] 7.10 `tests/infra/llm/test_tool_trace.py` — 主图工具 span 结构不回归（应为零改动）
- [ ] 7.11 `tests/agents/graph/test_direct_skill_round.py` / `test_verify_node.py` / `test_verify_material_source.py` — 直出轮与 verify 材料来源不回归
- [ ] 7.12 `tests/agents/skills/test_skill_executor.py` / `test_delegate_task.py` — 子角色改走装配入口后的装配断言
- [ ] 7.13 **新增**：`create_agent` 承载下工具取数正确 —— ①在 `create_agent` 图内调用 `ask_user`，断言注入状态为 `dict` 时仍取到 `query` / `kb_id` 且不抛 `AttributeError`；②`retrieve_kb` 的**迭代序号**在主循环（dict 状态）下为真实值、**不为 0**；③降级分支产出 warning（含工具名与缺失字段名）
- [ ] 7.14 **新增**：重生成轮的 system 段不丢 —— 首轮组装后经 `verify` 触发重生成，断言重生成轮的模型请求仍含完整 system 段（含未绑 KB 的第二条）且 `prompt assembled` 只产出一条
- [ ] 7.15 **新增**：五条日志的**产出次数** —— 一次生成内 `iteration done` / `iteration limit` / `model turn` / `prompt messages` / `prompt assembled` 的条数与变更前一致；含"上限轮恰好正常收尾也产出 `iteration limit`"这一情形
- [ ] 7.16 **新增**：取消/断连穿过"节点内 invoke 子图" —— 置位 `abort_signal` 后断言主任务收到 `CancelledError`、子图不遗留悬挂、`tool_trace.close()` 被调（评审 Important；须在**单元层**覆盖，不能只靠 E2E）
- [ ] 7.17 **新增**：委派放宽的两个方向 —— ①上限同轮声明 `delegate_task` 时该工具**被执行**（不被跳过）；②第 N 轮委派后第 N+1 轮未声明委派，有效上限**不回落**
- [ ] 7.18 **新增**：`capture.model_used` 捕获 —— 判别谓词改 `"model"` 后，`on_chat_model_end` 仍能把 `model_used` 写进 `capture`（`test_run_generation_tracing.py` 里点名断言，不只写"不回归"）
- [ ] 7.19 **新增**：生产链路端到端携带字段 —— 走"生产节点 → 子图 → 工具"的完整路径断言 `retrieve_kb` 取到**真实** `kb_id`、`ask_user` 取到**真实** `query`（SHALL NOT 用测试里手工塞入的 state 替代、也 SHALL NOT 落在降级分支）——这是形状修复的**残留风险**守卫
- [ ] 7.20 **新增**：温度分档取值 —— 绑 KB 与未绑 KB 各一条，断言传给模型的 kwargs 中温度档位正确（未绑档显式传配置值、绑 KB 档**不带 `temperature` 键**），并断言 `model turn` 日志的 `temperature` / `temp_source` 与之一致

## 8. 文档

- [ ] 8.1 `docs/agents/code-map.md` — `graph/` 结构（新增 `agent_factory.py` / `middleware.py`）与 `agent_node` 职责行
- [ ] 8.2 `docs/agents/data-flow.md` — 循环图、节点职责表、首轮消息组装路径
- [ ] 8.3 `docs/agents/api_contract.md` — `build_graph` 契约与新的装配入口签名
- [ ] 8.4 `docs/agents/glossary.md` — 新术语（装配入口 / 回合预算 middleware / system 提供方式 / 循环后阶段）
- [ ] 8.5 `docs/agents/prompt-ownership.md` — 补「system 段的施加通道」一句（组装仍在 `build_system_prompt`，下发经 middleware）
- [ ] 8.6 `docs/agents/defensive-patterns.md` — **已在本变更立项时登记**两条（「自定义 middleware 必须声明 `state_schema`，否则自定义键被静默丢弃」与「middleware 实例跨请求共享，per-request 状态不得存在实例属性上」）；实现期只需复核是否需要补充
- [ ] 8.7 `docs/agents/requirements_pool.md` — **已在本变更立项时登记** D-09（两个已核实死字段 `timings` / `_token_usage`）与 F-35（迭代触顶 → 空回答且状态 `complete`，修复归 `agent-round-budget`）；实现期只需复核
- [ ] 8.8 `docs/agents/logging-rules.md` — 若事件**产点**迁移需注明（事件名与字段不变）
- [ ] 8.9 撰写 ADR（一条：统一 agent 循环到 `create_agent`）；**编号在 `skill-execution-and-delegation` 写完之后取**，查 `dev-wsl` 当前最大号

## 9. 验收

- [ ] 9.1 `POSTGRES_HOST=localhost pytest tests/ -v` 全绿；`ruff format . && ruff check .` 无错误；`pyright src/` 不新增 error
- [ ] 9.2 无遗留 `print()` / TODO / 调试代码；`agent_node.py` < 400 行
- [ ] 9.3 真实模型 E2E（DashScope，绑 KB 与未绑 KB 各一次）：token 级流式内容正确、`[n]` 引用与 citations 一致、`extra_body`/思考档生效
- [ ] 9.4 真实模型 E2E：`/xxx` 直出轮与主 agent 委派轮各一次，确认子代理链路未回归（含委派折叠区事件）
- [ ] 9.5 触顶路径真实复现：宽泛问题触发触顶，日志含 `iteration limit`，**不新增**提示文案，`verify` 收到空答案后照常收尾（与变更前行为一致）
- [ ] 9.6 `agent_turn` generation observation 在 Langfuse 上仍出现且字段完整（迁 middleware 的验证）；失败则回退为命令式 span 并记录
- [ ] 9.7 **归档前置**：把本变更的 `delegate-task` delta 重新复制 `skill-execution-and-delegation` 落地后的 `fork 执行` requirement 全文再施加本节改动（否则会回退其工具面改动）
- [ ] 9.8 `openspec validate one-loop-two-roles` 通过；ADR 通过 `src/cli/check_adr.py`
- [ ] 9.9 **同步对方（已部分自然解决）**：`requirements_pool` F-36 所报的 `InjectedState` 缺陷，对方在 `skill-execution-and-delegation` 落地时**自己发现并修了 `retrieve_kb`**（`8014d64`）⇒ 原计划的"紧急同步"不再必要。剩余动作：把 F-36 的**收窄后状态**（`ask_user` 3 处 + 迭代序号，皆由本变更承接）在对方知情的前提下登记完毕（已在 `requirements_pool` 更新）
