# 简报：一循环两角色（主 / 子 Agent 同一循环）

> **用途**：交接给**新对话**的起点。旧对话在探索后期被"fork 工具面（allowed-tools）"带偏，本简报只保留与目标相关的内容。
> **性质**：对齐材料，**不是设计定稿**——设计由新对话产出。

## 一、目标

让主 Agent 与子 Agent 跑**同一个循环、两种角色**（对齐 claude-code / deepseek-harness），并把领域节点（`verify` / `format` / `agent_finalize` 的领域部分）**移出循环**。

**根因链（原始需求 → 目标）**：原始需求是"日志里 `ToolTraceCollector` 能否复用"；排查发现不可见的根因是**主 / 子不是同一个循环**——子代理是另一套 `create_agent`，其事件流被 `var_child_runnable_config.set(None)` 主动切断（`src/agents/skills/executor.py:189`），故本地日志与 Langfuse 都看不到子代理内部（ADR-0015 的「不解决的问题」已登记）。

## 二、目标形态的实证（两家参考）

- **claude-code**：子代理**复用同一个 `query()`**（`packages/builtin-tools/src/tools/AgentTool/runAgent.ts:776`），用 `createSubagentContext()` 派生上下文 + sidechain transcript + 独立 sub-trace（共享 sessionId）。
- **deepseek-harness**：子代理**用同一个 agent 工厂** `parent.ctx.agents.create(...)`（`packages/subagent/subagent-in-process-driver/src/index.ts:134`），跑在**独立 Session**。

⇒ 共同点：**一套 loop 代码、两种角色**；继承 / 隔离 / 观测的差异由**上下文与作用域**提供，**不是两套实现**。

## 三、本仓现状

主图 = 自建 `StateGraph`（`src/agents/graph/workflow.py`）：
`START → (agent ⇄ tools) → agent_finalize → verify → format → END`，另有 `skill_direct` 入口。

节点归属（新目标下要分「通用」与「领域」）：

| 节点 | 归属 | 依据 |
|---|---|---|
| `agent`（`make_agent_model_node`） | **混合**：循环机制通用；prompt 组装（persona / kb_domain / sources 段）、kb 温度分档 属领域 | `agent_node.py:148-317` |
| `tools`（ToolNode） | **纯通用** | `agent_node.py:320-334` |
| `agent_finalize` | **混合**：取 answer 通用；抄 `ctx.tool_contexts` / `temporal_years` 领域 | `agent_node.py:337-364` |
| `verify` | **纯领域**（KB 完整性 / 引用护栏 / 联网引导）；无接地工具时 no-op | `verify/node.py:20-62` |
| `format` | **纯领域**（`[n]` → citations 组装）；无接地时 no-op | `nodes.py:61-136` |
| `skill_direct` | 会话 / 技能域（另一入口） | `skill_direct.py` |

子代理 = 另一套实现：`create_agent`（`executor.py:272`），事件消费 = `fork_stream.consume_fork_events`（只处理 `on_chat_model_*`）。

## 四、推断的做法（待新对话验证）

```
通用循环（可复用给主 / 子）：agent(model+tools) ⇄ tools + 迭代上限 + 答案提取
        │
        └─ grounding 能力（工具在册才挂，只有主需要）：材料装载 → verify → format
```

**关键接缝**：**prompt 来源要参数化**（主 → RAG 组装；子 → fork 执行者提示），否则通用循环带不走两角色。

⚠️ 非定稿。核心待决：**统一到自建 `StateGraph`（参数化）** 还是 **统一到 `create_agent`**。

## 五、必须一并决定的前置：子代理边界七轴

两角色共用循环后，**边界语义仍需显式**（旧对话产出，仍有效）：

1. **身份** `subagent_id` 一等，随运行态传递，**不落单值字段**
2. **血统** `parent` / `depth`；单一 spawn 入口；本期 depth 上限 0
3. **继承** 显式白名单（逐项声明；本轮不继承父 history 需显式写明）
4. **隔离** messages / tool_contexts / 预算 / 日志线 / 记忆作用域各自独立
5. **观测** 父线只见 lifecycle 边界与最终结果；子内部步骤归子线、带 `subagent=` 判别（**环境注入**，由日志 helper 自动追加）
6. **生命周期** `start → running → end(reason)`；本期同步阻塞；取消传播；三层超时保持
7. **并发** 一轮 N 个子代理；per-child 状态不落单值字段

**未决（旧对话提到、未定）**：

- `ctx.child()` 具体继承哪些字段（现为 8 个"拍的"）
- 子代理材料**是否回传父**（模型委派路径不回传、`/xxx` 直出回传 —— 口径不一致）
- 观测**接线机制**（本地日志子线怎么接；Langfuse 侧独立 trace vs 子 span）
- 子代理预算 / 压缩、记忆作用域（机制未设计；记忆层未建）

## 六、明确出界（本目标不碰）

| 项 | 归属 |
|---|---|
| fork 工具面规则（默认继承 + 三减法） | **另一会话**的 change（暂名 `fork-tool-face-by-inheritance`） |
| 委派工具形态 A / B / C | 独立决策 |
| ~~**`skill_direct`（`/xxx` 直出轮）整体删除**~~ | **作废（2026-09-28）**：`skill-execution-and-delegation` 的 D4 明确「删除直出 → 否决」，且其 D5/D16 在直出路径上**加东西** ⇒ `skill_direct` 保留，见第十节决策 6 |
| 异步 / 后台子代理 | 依赖共享事件设施，延后 |
| 内容侧：agent vs skill 方法论分层、3 个 fork skill 的"预检索"描述 | 内容治理 |

## 七、素材移交：工具面规则（转另一会话）

旧对话已推导完整规则，另一会话可直接取用：

```
child_tool_face =
      继承主 agent 当前工具面（build_graph 写入 fork_tool_pool 的同一份快照）
    − FORK_FORBIDDEN_TOOLS（ask_user / delegate_task）
    − { t | readonly(t) 不是 True }（写类 + 只读性未知；fail-safe；未声明时构建期 warning）
    ∩ preset.tools（仅当声明）
    ∩ skill.allowed_tools（仅当声明；显式 [] = 收到零；loader 需区分「缺失」与「显式空」）
```

配套：未知名 **fail-loud**；`task_*` 注册进 `ToolRegistry` 并按只读性标注；排除写类工具是**本产品领域选择**（非对齐 CC）。

## 八、流程状态（新对话接手前须知）

- **ADR-0016 已作废（2026-09-26）**：`docs/adr/0016-subagent-contract.md` 已删除，`docs/adr/README.md` 索引行已还原（`check_adr` 15 条 0 error）。作废原因：它把"工具面"当主问题、且 D4 写"本期允许异构"，两者均与本目标相反。因当时**未提交**，作废零成本、**不占编号**。→ **编号 `0016` 现空出**。
- 仍有效的内容已全部收入本简报（七轴、工具面规则、作业清单），作废不丢信息。
- 旧对话的偏离：后期主轴换成了工具面（另一会话的活）。
- 新对话建议顺序：① 定"一循环两角色"的形态 → ② 决定契约七轴如何落 ADR → ③ 工具面交给另一会话。
- **worktree**：按 `docs/agents/dev-flow.md`「变更开工前置」先问，不得默认就地。建议新建独立 worktree（如 `corporate_rag-one-loop-two-roles`），与另一会话的 `corporate_rag-fork-tool-face` 并列。
- **交接时工作区现状（2026-09-26）**：旧对话在**主工作区** `/root/code/corporate_rag`（分支 `dev-wsl`，HEAD `b6e2f39`），**未产生任何提交**。机器上另有 worktree：`corporate_rag-fork-tool-face`（`feat/fork-tool-face-by-inheritance`，另一会话的工具面 change）、`corporate_rag-fork-result`（遗留）。`dev-wsl` 会被并行会话推进 → 提交有互踩风险，一律用显式路径 `git add`，**勿用 `git add -A`**（工作区有未跟踪文件）。

## 九、新对话的待决清单（起点）

1. 统一到哪套 loop：自建 `StateGraph` 参数化，还是统一到 `create_agent`？
2. prompt 来源接缝怎么设计（通用循环如何带两角色的 system 来源）？
3. `verify` / `format` 归位：作为"接地能力贡献的循环后阶段"，还是别的形态？
4. `agent_finalize` 拆分（通用出口 vs 领域材料装载）？
5. 七轴契约与新形态的 ADR 划分（一条还是多条）。
6. 与另一会话的编号 / 顺序协调（0016 空出后归谁）。

## 十、决策记录

> 由新对话（2026-09-28）逐条推进，本节只记**已定**的条目，未定的仍留在第九节。

**决策 1（形态骨架）—— 已定：A + B1**

- **A：L1 统一到 `create_agent`。** 主/子角色共用同一个装配工厂（`model` + `tools` + `name` + `system_prompt 来源` + `middleware` + 上限参数）。理由：官方 v1 明确 `create_agent` 是「构建 agent 的标准方式」；claude-code（`query()` 单函数）与 deepseek-harness（`ctx.agents.create()` 单工厂）均为一套循环两角色；`create_agent` 本身即 `CompiledStateGraph`，`@dynamic_prompt` / `wrap_model_call` / `state_schema` 足以承载我们的参数化面。
- **L3 领域阶段（`verify` / `format` / `agent_finalize` / `skill_direct`）留在外层图**，不迁入 middleware。
- **B1：Langfuse 单 trace 嵌套**（不是独立 sub-trace）。理由：`trace_id` 是本仓对外契约（响应头 `X-Trace-ID` / 日志第三段 / SSE done 字段）**且就是 Langfuse 的 trace 主键**，独立 trace 会让二者脱节；本仓 langfuse 是 v2 SDK（2.60.10），`@observe` 无 `trace_id` 参数、独立 trace 只能命令式，成本高一个量级；单 trace 与 A 的「一套实现两角色」同构（一套观测两角色）。判别键用 `metadata.lc_agent_name`（由 `create_agent(name=…)` 自动注入），不自造。
- **A 的落地代价（已定位）**：`agent_service.py:250/267/277` 的 `langgraph_node == "agent"` 须改 `"model"`；`ToolTraceCollector` 的 `langgraph_node == "tools"` 名字不变但会开始收到子代理的 tools 事件，须按 `lc_agent_name` 分流；**A 之后主/子节点名相同，角色判别只能靠 `lc_agent_name`**。
- **B 的额外前提（实测修正）**：本仓 Langfuse 采集是**装饰器 / 命令驱动，不是回调驱动**——全仓无 `CallbackHandler`、无 `get_current_langchain_handler()`；fork 路径当前**零 observation**。故「子代理在 Langfuse 不可见」不是挂错 trace，而是没挂；B1 要新增的是 fork 路径的 `@observe` + `metadata.subagent`。

**决策 1 的调研依据**：`docs/tmp/deep-research-one-loop-two-roles.md`（含官方一手引用、两参考实现源码行号、本仓探针实证）。

**决策 2（prompt 来源接缝）—— 已定：2b**

- 接缝 = 一个 `prompt_source` 参数，**只产 system**；其余首轮消息（注入 / 历史 / 当前 user）由调用方 seed 进 state。
- 主角色 `prompt_source = build_system_prompt(...)`；子角色 `= _executor_system_prompt(preset)`（人设 + `FORK_EXECUTION_CONTRACT`，子角色今天已是这形状）。
- **未绑 KB 的第二条 SystemMessage**：用自定义 `wrap_model_call`（而非 `@dynamic_prompt` 糖）保住两条。**实测通过**：模型实收 `[SystemMessage(FIRST), SystemMessage(SECOND), HumanMessage(hi)]`，与今天逐字节同形（`@dynamic_prompt` 只能给单条）。
- 明确**不在本次动**：`_truncate_history` 的窗口口径（写死 8000 vs 配置 32768，属独立缺陷）、`clean_prefix` 的读时清洗。

**决策 3（`verify` / `format` 归位）—— 已定：3a（形态不动）**

- 位置 = **外层图的循环后阶段**（`agent → agent_finalize → verify ⇄ → format → END`）。不是循环内（判决需整轮答案），也不是图外（verify 需要回边重生成，出图就要在 service 层自写重生成循环，更差）。
- 归属表述改为中性的「**循环后校验与引用组装阶段**」：判据读本轮材料池（`_has_web_context` / `_has_kb_context` / 年份 / `[n]`），不由任何能力注册。
- **不采纳 3b**（把「无接地产出 → 直接 END」提到条件边）：不是新能力只是图更好看，且会顺带砍掉 no-op 分支的既有可观测事件（`Event.SKIP reason=guard_pass`、`FORMAT_DONE reason=abstention/no_markers`）。
- ⇒ 「两入口共用（`agent_finalize` 与 `skill_direct` 都汇到 verify）⇒ 后阶段挂在材料池而非循环」这条论证**成立**——`skill_direct` 已确认保留（2026-09-28，见决策 6）。
- 附带发现（A 之后可简化）：regen 决策里显式返回 `_agent_iterations=0` + `_delegate_used=False` + `ctx.web_count=0`，注释称「每段 regen 轮全新预算」。**更正（2026-09-28 评审）**：前两者是**三处**——`guardrails.py:105/106`、`:180/181`、**`regen_decision.py:174/175`**；`ctx.web_count = 0` 是**两处**（`guardrails.py:99`、`regen_decision.py:169`，与主循环预算无关故**保留**）。A 之后 regen = 重新 invoke `create_agent` → 计数与委派标志天然从初值起 ⇒ 前两者**可删**（已由探针 H 证实）。

**决策 4（`agent_finalize` 拆分）—— 已定：不拆**

保持为外层图的**单个节点**，职责不变（通用：取末条 AIMessage 文本为 answer；领域：把主 ctx 的 `tool_contexts` / `temporal_years` 快照进 state）。理由：① 「拆成通用出口 + 领域装载」的前提**不成立**——子角色根本没有这个节点（子角色的产出聚合在 `fork_stream.consume_fork_events`，属 `skill-execution-and-delegation` 的改写面）；② 材料池快照进 state 是 `skill_direct` 路径的**既有必需**（来源：`2026-09-11-agent-skill-execution-layer` 计划的 R5——verify 的判据统一读 state，否则直出轮空转），拆开只是位移、无收益；③ 一个概念一个位置（「本轮产出提取」），且与他们的 change 零交集。

**决策 5（ADR 划分）—— 已定：一条 ADR**

`统一 agent 循环到 create_agent（L1/L2 参数化，领域阶段留外层图）`。

- 2b（prompt 接缝）/ 3a（verify·format 归位）/ 4（finalize 不拆）都是这条决策的**实现细节或推论**，不单立。
- **观测（B1）不写第二条**：其决策权已在 `skill-execution-and-delegation` 的 D11 落定（同为单 trace 嵌套、`scope=delegate` 分域），我方只是采用 → 重复写会造成「两个 ADR 说同一件事」，且并行分支少占号更安全。
- **七轴契约进 spec，不进 ADR**：它是契约/规格（描述「应当怎样」），不是不可逆取舍。
- ⚠️ **编号须在对方写完之后取**（现 max `0015`，他们会先占 `0016`）；建 ADR 前查 `dev-wsl` 当前最大号，且 `check_adr` 要求连续 → 重编号必须与合并同一提交。

**决策 6（排序与编号协调）—— 已定（用户 2026-09-28）**

- `skill_direct` **保留**（依据 `skill-execution-and-delegation` 的 D4：`删除直出 → 否决`；且该变更 D5/D16 在直出路径上**加东西**）⇒ 决策 3 的「两入口共用」论证成立，简报第六节「skill_direct 将被删除」一栏作废。
- 优先级：**`skill-execution-and-delegation` 最高 → 本 change 第二 → 其余（`agent-round-budget`、`fork-*` 草案等）之后**。冲突后置解决，本 change 不为避让而缩范围。
- 本 change 与对方 change 的**文件重叠面**（据其 proposal「Impact」核对）：`src/agents/skills/executor.py`（其 D7/D11/D15/D6 全在此）· `src/services/agent_service.py` · `src/config/const.py` · `src/infra/llm/tool_trace.py` · `src/agents/skills/fork_stream.py` · `src/agents/skills/delegate_task.py` + 我们的 `src/agents/graph/workflow.py` / `state.py` / `agent_node.py`。

**A 的成本实测修正（2026-09-28 探针，langgraph 1.2.9）**：把 `create_agent` 作为外层图节点后——

| 观测点 | 实测 | 对 SSE 层的影响 |
|---|---|---|
| 内层 `on_chat_model_*` / `on_tool_*` | `langgraph_node` = `model` / `tools` | `agent_service.py:250/267/277` 的 `== "agent"` 须改 `== "model"`（**小改，非重写**） |
| 外层节点名落点 | `metadata.checkpoint_ns` = `agent:<uuid>` | ⚠️ **评审否决**：不用它作判据（属过度匹配——来源命名空间一变就会误吸）。判据只用「`langgraph_node == "model"` + `scope == "main"`」 |
| 域节点 `format` / `agent_finalize` | 仍是外层名、`checkpoint_ns` 为空 | **过滤条件不变** |
| `ToolTraceCollector` 的 `== "tools"` | **仍命中** | **零改动** |
| token 级流式 | `on_chat_model_stream` **仍在** | SSE token 交付保住（仅验事件形状，token 内容未验） |

**未决**：无（第九节 1~6 条全部有结论）。剩余动作 = 落 spec → 自审 → 用户评审 → 落 ADR（编号待对方定）→ writing-plans。

## 十一、A 的可行性探针结果（2026-09-28，langgraph 1.2.9 / langchain 1.3.11，假模型）

三个探针全部跑完，四条硬结论：

### 1. ⚠️ 事实纠正：**今天没有图级步数硬兜底**

| config 形态（单个 `create_agent`，需 41 步才收尾） | 结果 |
|---|---|
| 完全不传 config | **完成**（41 步，无上限） |
| 传 `config={}` | **完成**（41 步，无上限） |
| 传 `config={"recursion_limit": 25}` | **GraphRecursionError**（13 次模型调用 ≈ 26 步） |

⇒ **只有显式传 `recursion_limit` 才有兜底**；`None` 与 `{}` 都等于无上限。而 `agent_service.py:700` 的 `graph.astream_events(initial_state, version=…)` **没传 config** ⇒ **今天的生产也没有图级硬兜底**（此前简报与本对话把它当"25 步硬垫"，是错的）。今天的循环约束只有两个软上限：`MAX_AGENT_ITERATIONS=5`（`route_agent` 内）与 `MAX_VERIFY_REGENERATIONS=2`。

### 2. 嵌套时外层 limit 管不到内层

外层 `recursion_limit=6` 时，节点内 `create_agent` 子图**跑满 41 步照样完成**；显式给内层传 `recursion_limit=8` 才立刻生效（4 次模型调用即 error）。⇒ **A 之后若要兜底，必须显式给内层传 limit**；按最小改动原则本变更**不新增**该兜底（属 `agent-round-budget` 的题），但 **spec 须写明"当前无步数硬兜底"**，免得后人以为有。

### 3. 消息回写策略：`suffix`（或 `full`）都干净，`last` 丢中间消息

seed = `[H(历史问), A(历史答)]`，循环内 1 轮工具 + 1 轮收尾：

| 策略 | 父 `state.messages` | 判断 |
|---|---|---|
| `suffix`（只回写子图新增段） | `len=5` `[H, A, A, Tool, A]` | ✅ **推荐**（显式，不依赖身份保持，与今天语义一致） |
| `last`（只回写末条 AIMessage） | `len=3` `[H, A, A]` | ✗ 丢 Tool 与中间消息 |
| `full`（整包回写） | `len=5` `[H, A, A, Tool, A]` | ✅ 也干净，但**依赖 seed 对象/身份被原样带出** |

**去重机制实测**：`add_messages` 按 **message id** 去重——同一对象喂三次 → 1 条；两条**内容相同但不同对象**（无 id）→ 2 条。`full` 之所以不重复，是因为 `create_agent` 把输入消息**按引用**带出（同 id）。

**regen 场景验证通过**（`suffix`）：`verify` 注入的 `SystemMessage` 保留、首轮历史不重复、第二轮模型确实看到了指引（第二轮后 `len=5`：`[H(问), A(答), A(上轮答), S(指引), A(新答)]`）。此消息序与今天 `state.messages` 的累积方式一致。

### 4. 温度 / `extra_body` 走 middleware 成立，但有两条坑

- `request.override(model_settings={...})` → 模型**实收** `{'temperature': 0.6, 'extra_body': {'enable_thinking': True}}`；逐次调用取当时的值 ⇒ **kb 温度分档可 per-call 变化** ✅（`factory.py:1361/1391/1404` 走 `model.bind(**model_settings)`）
- ⚠️ **坑 1**：今天 KB 档是「**不传** temperature」，而 `model_settings={"temperature": None}` 会被**原样传下去**（实测）⇒ 必须用「**不带该键**」表达"不传"，不能用 `None`
- ⚠️ **坑 2**：只实现同步 `wrap_model_call` 时，在异步上下文（`ainvoke`/`astream`）会抛 `NotImplementedError: Asynchronous implementation of awrap_model_call is not available` ⇒ **middleware 必须实现 `awrap_model_call`**（`@dynamic_prompt` 之类的装饰器会自动生成两者；2b 若手写则须自己写异步版本）

**仍待设计/待写清**（探针未覆盖）：① 首轮三个组装步骤（`_truncate_history` / `SKILL_INJECTION_PREFIX` 抽取 / `clean_prefix`）在 A 之后的落点；② `_agent_iterations` / `_max_agent_iterations` / `_delegate_used` 三个状态字段与 `route_agent` 的去向；③ `Event.PROMPT_MESSAGES` / `PROMPT_ASSEMBLED` 的产点迁移（计数须不变）；④ 迁 middleware 的事件字段集须逐字复现（import 期硬断言）。

## 十二、A 的实现落点设计（4 项，2026-09-28）

> 前提探针：见第十一节。本节是**结论**，落 spec 时按此展开。

### ① 首轮三步骤落点：**全留在 `agent` 节点，一次组装、产出两半**

节点仍调 `build_prompt(...)`（保留该函数与其契约测试 `tests/rag/test_prompt_contract.py`），然后**按类型拆分**：

- **system 段** → 经**外层 `AgentState` 的新增声明字段**携带（跨 invoke 持久），由 prompt middleware 每次模型调用读出并施加。⚠️ **更正（2026-09-28 评审 Blocker）**：原先写的"经**子图 state** 携带"是**错的**——子图每次 `invoke` 即销毁，而**重生成轮**（`verify` 触发回边）节点按"`state.messages` 非空即不组装"的既有语义不再组装（`agent_node.py:176-179`），载体若在子图 state 则重生成轮**拿不到任何 system 段**（未绑 KB 的禁止检索、引用纪律、人设全部静默消失）。放外层字段后重生成轮照常取到，且不触发第二次组装。
- **非 system 段**（注入 + 历史 + 当前 user）→ 作为子图 input `messages`

⇒ 红利：`build_system_prompt` **每轮只调一次**（今天也是），`PROMPT_ASSEMBLED` / `PROMPT_MESSAGES` 的**产点与计数都不变**（见 ③）。

⚠️ **修正 item 2 的措辞**：接缝不是「`prompt_source` callable 每请求组装」，而是「**`build_agent` 的 system 提供方式**」两形态——**静态串**（子角色：`_executor_system_prompt(preset)`，今天就这样，无需 middleware）/ **state 携带 + middleware**（主角色）。**2b 仍成立**：自定义 `awrap_model_call` 以保住 1~2 条 system（`@dynamic_prompt` 只能给单条）。另：若改走"middleware 内组装"，则 `build_system_prompt` 会**每模型调用执行一次** → `PROMPT_ASSEMBLED` 每轮多发 N 次，故不可取。

### ② 状态字段与 `route_agent` 去向：**全部迁入预算 middleware，函数整个删除**

| 今天 | A 之后 |
|---|---|
| `route_agent`（`agent_node.py:367-391`） | **删除**——其三条判断全由 create_agent 内建条件边 + 预算 middleware 的 jump 接管 |
| `AgentState._agent_iterations` | 迁入预算 middleware 的 `state_schema`（如 `_turn_count`） |
| `AgentState._max_agent_iterations` | 变成 middleware **构造参数**（取值 `MAX_AGENT_ITERATIONS`） |
| `AgentState._delegate_used` | 迁入 middleware 的 state；`after_model` 检测本轮 `AIMessage` 是否含 `delegate_task` tool_call → 有效上限 = `MAX_AGENT_ITERATIONS + MAX_DELEGATE_BONUS` |
| `Event.ITERATION_DONE` / `ITERATION_LIMIT` / `MODEL_TURN` | 产点迁入 middleware（字段集见 ④，逐字不变） |

`const.py` 的 `MAX_AGENT_ITERATIONS` / `MAX_DELEGATE_BONUS` **保留**（成为 middleware 参数）。**不动**迭代触顶的既有缺陷（空回答 + `complete`）——那归 `agent-round-budget`。

### ②附 迭代上限 middleware 的**必须形态**（探针 G 定案）

```
class AgentTurnBudget(AgentMiddleware):
    state_schema = <声明 _turn_count / _delegate_used / query 等全部自定义键>
    def after_model(state, runtime):        # ← 判定必须在 after_model
        n = state._turn_count + 1
        if n >= effective_limit and 末条 AIMessage 含 tool_calls:
            log(ITERATION_LIMIT, query=state.query, iteration=n)
            return {"jump_to": "end", "_turn_count": n}      # ← 不注入任何消息
        return {"_turn_count": n}
```

三条硬约束（各有探针依据）：

1. **必须声明 `state_schema`**，凡自定义键都要在里面——否则**静默丢弃、不报错**（探针 D2/E/F 三次复现；E 的"不能 jump"结论就是被它误导的）
2. **判定必须在 `after_model`**：用 `before_model` 判下轮会让**多执行一次工具**、末条变 `ToolMessage` → `_extract_text` 会把**工具结果当成答案**（用户可见倒退）
3. **不得用官方 `ModelCallLimitMiddleware`**：其 `exit_behavior="end"` 会**注入一条 AIMessage 文案**（英文限流提示会变成用户答案）

对齐验收：**模型调用 = limit 次、工具执行 = limit−1 次、末条 = 含 `tool_calls` 的 `AIMessage`**（= 今天 `route_agent` 的时点）。

### ③ `PROMPT_*` 产点：**零变化**（① 选型的最大红利）

| 事件 | 产点 | 计数 |
|---|---|---|
| `prompt assembled` | **不变**（仍在 `build_system_prompt` 内） | 不变 |
| `prompt messages` | **不变**（仍在节点的组装路径末） | `system_msgs` 在节点手上（① 已保证），三个计数齐 |

### ④ 迁 middleware 的事件字段集（逐字复现，两处同名登记 + import 期硬断言）

| 事件 | 字段集（逐字） | 新产点 |
|---|---|---|
| `iteration done` | `(iteration, msgs)` | `awrap_model_call` 入口 |
| `iteration limit` | `(query, iteration)` | `after_model` 的 jump 分支 |
| `model turn` | `(model, usage_in, usage_out, usage_estimated, fallback, latency_ms, iteration, temperature, temp_source, kb_bound)` | `awrap_model_call` 出口（`kb_bound` 读 `current_request_ctx`）|
| `prompt messages` / `prompt assembled` / `graph compiled` | 见 ③ | 不变 |

`MODEL_TURN` 的 `temperature` / `temp_source` 由 middleware 自己决定（它就是分档的那一方）；**"不传 temperature"要用「不带键」表达，不能用 `None`**（探针 C 坑 1）。

### A 之后仍未验的（留给实现期）

- `@observe`（`agent_turn` generation span）放进 middleware 是否成立——需验证 `langfuse_context.update_current_observation` 在 `awrap_model_call` 里指向正确的 observation（审计第 6 条）
- 真实模型（DashScope）E2E：token 流式内容、`extra_body`、温度分档的真实效果

## 十三、B/C 补完：落点与影响面（2026-09-28）

> 第十一节是探针，第十二节是设计；本节把**落点**与**影响面**补齐，作为 proposal 的直接输入。

### B1 两个硬约束

| | 事实 | 处置 |
|---|---|---|
| **文件红线** | `agent_node.py` 现 **391 行**；A 要加 3 个 middleware 类 + 装配工厂 ⇒ 必超 400 行红线（CLAUDE.md） | **拆两个新文件**：`graph/agent_factory.py`（`build_agent` 装配工厂）· `graph/middleware.py`（三件套：system 施加 / 回合预算 / Langfuse span）。`agent_node.py` 删掉 `make_agent_model_node` / `make_agent_tools_node` / `route_agent` 后**缩到约 200 行** |
| **middleware 实例跨请求共享** | 主角色的 middlewares 随图在 service 启动时构造**一次** | **禁止把 per-request 状态存在实例属性上**（会跨请求串号）；per-request 数据只走 `state_schema` 或 `current_request_ctx`。⚠️ 必须写进 spec |

### B2 五项落点

1. **`build_graph` 新形态**：`add_node("agent", make_agent_loop_node(bundle))`（节点名保持 `"agent"` 以便 `route_verify` 回边不变）；`tool_sink` 保留在原处（`:857-863` 的 `fork_tool_pool` 供给链不动）；`delegate_task` / `skill_direct_node` 注入参数不变
2. **`Event.GRAPH_COMPILED` 只准发一次**：仍由 `build_graph` 末尾发（`workflow.py:132`）；**`build_agent` 不得发**——子角色每次委派都调用它，否则每委派多一条日志
3. **角色 `name`**：主 = `"main"`；子 = skill 名（通用委派待定，**以 `skill-execution-and-delegation` 的 D6 结论为准**）
4. **regen 轮计数是否重置**：**实测通过**——每轮 `invoke` 得到全新 state，计数天然从 0 起（第 2 轮计数 = 2 而非 4）⇒ 第十一节第 3 条说的"复位管道可删"**成立**。附带：探针同时**精确复现了既有缺陷**（末条含 `tool_calls`、`answer=''`）
5. **`abort` / `ask_user` 进子图**：机制上**预期不变**（工具仍 await 同一个 `ctx.clarify_channel`/`pending_asks`；`asyncio.gather` 只影响 ContextVar 的「写」不外传，本仓工具靠**共享对象**通信）——留真实 E2E 验证

### B3 字段清理清单

| 字段 | 处置 |
|---|---|
| `_agent_iterations` / `_max_agent_iterations` / `_delegate_used` | **迁入预算 middleware 的 `state_schema` / 构造参数**（设计已定） |
| `timings` | **死字段**（`state.py:55` 声明，全仓零使用）——不在本变更混入，仅登记 |
| `_token_usage` | **死字段**（`state.py:52` 声明，全仓零使用）——同上 |
| `_ask_count` | 仍活（4 src / 9 tests）——不动 |
| `is_fallback` / `model_used` / `deep_thinking` | 广泛使用——不动 |

### C1 测试面：12 个文件受 A 影响

```
tests/agents/graph/   test_agent_node.py · test_graph.py · test_state.py
                      test_verify_node.py · test_verify_material_source.py · test_direct_skill_round.py
tests/services/       test_agent_service.py · test_run_generation_tracing.py · test_dual_stream.py
tests/agents/skills/  test_delegate_task.py · test_skill_executor.py
tests/infra/llm/      test_tool_trace.py
```

（`test_graph.py` 还 mock 了 `make_rag_tools`（`:726`），`build_graph` 一改签名就波及）

### C2 文档面：6 处

`code-map.md`（`graph/` 结构 + `agent_node` 职责）· `data-flow.md`（循环图/节点表/首轮三步骤）· `api_contract.md`（`build_graph` / `make_agent_model_node` 契约）· `glossary.md`（新术语）· `prompt-ownership.md`（system 段的**施加通道**补一句）· `defensive-patterns.md`（见 C3）

### C3 必须登记的「可复发缺陷类别」

> **自定义 middleware 的 `state_schema` 静默丢弃未声明键**——不在 `state_schema` 里声明的键，读写都**不报错、直接丢**。本次探针**连踩四次**（D2/E/F/H），其中一次把我误导成"`after_model` 不能 jump"的错误结论。凡用 middleware 承载计数/数据，**必须先声明 `state_schema`**。

### C4 必须加一条「缺陷不被改坏」的验收

> A 之后须断言「**触顶时末条为含 `tool_calls` 的 `AIMessage`、`answer` 可为空串**」——这是今天的行为，也是"空回答 + `complete`"缺陷的组成部分。探针 H 证明该设计**精确复现**了它；不写这条断言，后人会以为 bug 已修或以为 A 改坏了。

### C5 规格 delta 集（据主规格逐条核对，附命中行）

| capability | 类型 | 命中依据 |
|---|---|---|
| `delegate-progress-observability` | **MODIFIED** | `spec.md:8` 明写判据是 `metadata.langgraph_node == "agent"` ⇒ A 把主循环节点名改成 `model`，该字面引用失效 |
| `answer-verification` | **MODIFIED** | `spec.md:66/71` 明写「`_agent_iterations` 上限 SHALL 只管主循环」「重生成不受 `_agent_iterations` 上限影响」⇒ 字段迁移后须改为行为化表述 |
| `delegate-task` | **MODIFIED** | `spec.md:42`「`create_agent` 的 middleware 参数保留装配位但**默认传空（v1 不启用）**」⇒ 本变更启用。⚠️ **该 capability 同时被 `skill-execution-and-delegation` 修改，sync 顺序须在其后** |
| `agent-loop-observability` | **ADDED** | `spec.md:33/36` 只规定"命中记录 warn 日志"，**未规定触顶路径的确切消息语义** ⇒ 新增一条把「模型调用 = limit 次 / 工具执行 = limit−1 次 / 末条含 `tool_calls` 的 `AIMessage` / answer 可为空」钉住（C4 的依据） |
| `prompt-composition` | **MODIFIED** | `spec.md:11` 规定组装由 `build_system_prompt` 完成且两处调用点都走它（**A 不破坏**）；但 `:79`「默认行为逐字不变（端到端快照）」**未规定"系统段的施加通道"** ⇒ 补明「system 段送入模型请求时 SHALL 逐字不变，含未绑 KB 时的**第二条** system 消息」 |

**明确不改 delta 的**（逐条核对过）：`agent-state-definition`（不列举被删字段）· `chat-temperature-policy`（行为不变：同请求同档位，实测 per-call 生效）· `agent-service`（改的是一处过滤谓词，该 requirement 未指名节点名；其 scenario 里的 `classify/rewrite/...` 节点名**早已陈旧**，属另一个问题）

### C6 新建 capability `agent-assembly`（写 delta 时的补充决定）

落 delta 时发现：`delegate-task` 的改动需要指向一组**无处安放的契约**——主/子角色共用同一装配入口、差异仅由参数提供、装配入口不产出编译日志、middleware 不得持 per-request 实例状态、回合上限参数化且委派轮放宽。它们不属于任何现有 capability（既不是 `delegate-task` 的子集，也不属 `agent-service`），故**新建 `agent-assembly`**（5 条 requirement / 8 个 scenario，全部 ADDED）。

⇒ 最终 delta 集 = **1 新建 + 5 修改**（`agent-assembly` 新建；`delegate-progress-observability` / `answer-verification` / `delegate-task` / `prompt-composition` / `agent-loop-observability` 见 C5 表）。凡属"行为保持的实现细节"（如 `build_agent` 的签名、middleware 类别划分）一律进 `design.md`，不进规格。

## 十四、独立架构评审与修复（2026-09-28）

**评审**：独立子代理（未写过提案、只读型；已核对**未越权写入**工作区）。结论 **Request changes** = 2 Blocker + 7 Important。提案落点 `docs/openspec/changes/one-loop-two-roles/`。

**两条 Blocker（我已独立复现）**：

1. **工具经 `InjectedState` 取数在 `create_agent` 下崩**：该注入的形状由**承载它的图**决定——外层自建图给 `AgentState` 实例，`create_agent` 给 **`dict`**（自跑对照探针：`create_agent(state_schema=<dataclass>)` 与默认 schema **都**注入 dict）。本仓 5 处属性访问（`rag_tools.py:102/187`、`ask_tools.py:82/83/166-167`）会 `AttributeError`，被错误回喂吞成 `ToolMessage` 错误 ⇒ **检索恒空、澄清恒失败**，而图照常跑完、日志看不出异常。⇒ 归 `design.md` **D11**；已登记 `requirements_pool` **F-36**（**对方 D7/D8 会先中**）；task 2.6~2.8 + 7.13。
2. **重生成轮 system 段无着落**：我把 system 段放**子图 state**，而子图每次 `invoke` 即销毁 + 重生成轮不再组装（`agent_node.py:176-179`）⇒ 重生成轮**拿不到任何 system 段**。⇒ 改放**外层 `AgentState` 的新增声明字段**（跨 invoke 持久）；见 D2、task 2.1/2.2/4.1、7.14。

**七条 Important 的处置**：

| # | 问题 | 处置 |
|---|---|---|
| 1 | `iteration limit` 今天**与 tool_calls 无关**（`agent_node.py:300-303`），middleware 版会少发 | 日志与 jump **解耦**："计数达上限即记"；D3 + spec 加 scenario + task 7.15 |
| 2 | regen 复位是**三处**不是两处（含 `_delegate_used: False`） | 更正为三处；`ctx.web_count` 三处**保留**；task 4.2/4.4 |
| 3 | `agent-assembly` 的「不得存在第二套装配」与 tasks "允许中间态"**规格自相矛盾** | **收口取 (a)**：§5 设为必须，对方 P2 未落地则**不开工**（闸门）；task 0.2 |
| 4 | 委派放宽只做"本轮检测"会**丢余量**、只做"持久标志"会让**上限同轮的委派根本不执行** | 明确"本轮命中**先置位** OR 此前已置位"两者兼有；spec 加两个 scenario；task 7.17 |
| 5 | 验收不足以证明"对外行为不变" | 补 4 项断言（取消穿子图 / `create_agent` 内工具 / 五条日志**产出次数** / `capture.model_used`）：task 7.13~7.18 |
| 6 | `checkpoint_ns` 兜底属**过度匹配且有害** | **去掉**；判据只用「节点判别键 + `scope=="main"`」；同时更正 `agent_service.py:186-208` docstring；task 3.1/3.2 |
| 7 | 漏评估备选「参数化自建循环」 | 已评估并否决，三条理由记入 `design.md` **D12**（与主规格冲突 / 与在途 change 对撞 / 工具修复本就逃不掉） |

**未采纳评审的部分**：无（7 条 Important 全部按建议修）。

**评审同时指出仍未验证的**（已如实写进 `design.md` 的 OQ/Assessment）：`awrap_model_call` 内 `@observe` 指向、真实模型 token 内容与 `extra_body`/温度、`abort` 穿过"节点内 invoke 子图"的取消传播（已要求**单元层**覆盖，不只 E2E）、重生成轮 system 施加的端到端形态。

## 十五、聚焦复审与第二轮修复（2026-09-28）

**复审**：另一个独立子代理（未参与写作与上一轮评审），只验九条是否真关闭 + 有无引入新矛盾。结论 **Request changes**：**九条实质关闭**（含行号逐条核对吻合），但**修复自身引入了 3 处新矛盾**，且 **Blocker 1 留有未被覆盖的残留路径**。

**新矛盾（已修）**：

| # | 问题 | 修法 |
|---|---|---|
| a | `ctx.web_count = 0` 我写成"三处"，实际**只有两处**（`guardrails.py:99`、`regen_decision.py:169`）——机械照搬了前者的"三处" | proposal / design / tasks 全部改为两处 |
| b | `agent-loop-observability` 我新写的"上限轮正常收尾时**工具执行次数为零**"与其自有 scenario 及 `route_agent` 逻辑**互相矛盾**（要走到 N=上限，前 N−1 轮必然都声明并执行了工具） | 改为"工具执行次数 = 有效上限 − 1；上限为 1 且首轮即收尾时才为零" |
| c | "第一条 system 在 messages 还是字段"在 `design.md` D2 与 `tasks.md` 1.3 之间**表述互斥**（我只写了前插第二条，隐含第一条已在 `messages` 里，且顺序会变成 `[第二条, 第一条]`） | 钉死机制：`override(system_message=<第一条>, messages=[<第二条>, *request.messages])` → 实收 `[第一条, 第二条, …]`，与 `prompt.py:248-250` 顺序一致 |

**残留路径（Blocker 1 的真正要害，已修）**：我只修了工具的**取数形状**，**没把工具依赖的字段传进子图 state**——`retrieve_kb` 读 `state.kb_id`、`ask_user` 读 `state.query`/`state.kb_id`，而子图 schema 只列了 `query`/system/计数/委派标志，**漏了 `kb_id`**。即便形状兼容，也会走"显式降级"分支 ⇒ **kb_id 恒空、检索恒空**（症状与原始 Blocker 相同，只是不再抛异常）。
⇒ 修法：`agent-assembly` 加一条"装配 SHALL 把工具运行所需字段传入图状态"+ 一条**生产链路端到端** scenario（不许用手工塞入的 state 替代）；tasks 1.2 钉死键名并**必含 `kb_id`/`query`**、2.2 明确 seed 清单、新增 7.19。

**另两条遗漏（已补）**：③ 温度分档**无回归断言**（6.5 要求实现但 tests 无一条断言取值）→ 新增 7.20；④ 计数键**无唯一 canonical 名**（middleware 与工具靠隐含约定对齐）→ 在 tasks 1.2 钉死 `_turn_count` / `_delegate_used` / `_system_messages`，2.6 改为引用该键名。

**键名统一表**（防实现期走样）：`_system_messages`（system 段，跨 invoke）· `_turn_count`（回合计数）· `_delegate_used`（委派标志）；子图 schema **必含** `kb_id` / `query`。

### 第三轮：窄范围核验 → **Approve**（2026-09-28）

另一个独立子代理，只核验第二轮点名的 4 处 + 2 条附带，不做全量评审。结论：

| 核验项 | 裁定 |
|---|---|
| ① `ctx.web_count` 处数（两处）+ `_agent_iterations`/`_delegate_used`（三处） | **已到位**（源码实测两处/三处，三份文档一致） |
| ② `agent-loop-observability` 的"工具执行次数" | **已到位**（与同文件 scenario 及 `agent_node.py:384/389` 的 `route_agent` 逻辑自洽） |
| ③ system 施加机制在 D2 与 tasks 1.3 | **已到位**（两处逐字相同；且与 `prompt.py:248-250` 的顺序一致；另经源码确认 `factory.py:1416-1417` 确有 `messages = [request.system_message, *messages]`） |
| ④ 工具依赖字段传入子图（Blocker 1 残留） | **已到位**；核验人**自行全仓核对** `InjectedState` 的 state 依赖**只有** `kb_id`/`query`/迭代序号三项，无第四项遗漏（`web_tools.py` 的 `search_web` 无 `InjectedState`） |
| 附带 1 温度分档断言 / 附带 2 计数键 canonical 名 | **均已到位** |

**新引入矛盾：无。** 仅两条非阻断措辞建议（"次数为零"限定偏窄、责任实体写作"装配入口"而实际落在节点）——**也已顺手改掉**：前者简化为"有效上限减一"（该式对上限为 1 同样成立），后者改为"装配产出的运行链路 SHALL……由调用方在构造图输入时 seed"。

**最终状态**：`openspec validate one-loop-two-roles` 通过；4/4 工件完整；`0/76` 任务未开工。**提案已达 Approve，可进入 ⑥ 执行阶段**（需先退出 explore 模式；且 §0.2 的开工闸门要求 `skill-execution-and-delegation` 的 P1+P2 已落地）。
