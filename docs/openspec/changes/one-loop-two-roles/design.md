## Context

**现状（已逐行核对）**：主 agent 与 fork 子代理是**两套循环实现**——主图自建 `StateGraph`（`src/agents/graph/workflow.py:72-133`：`agent ⇄ tools` + `agent_finalize` + `verify` + `format`，另有 `skill_direct` 入口），子代理由 `create_agent` 生成（`src/agents/skills/executor.py:272`）。两边已经出现行为分叉：主 agent 触顶只记日志（`agent_node.py:300-303`，无兜底）、子代理触顶有兜底文案。

**上游对齐**：claude-code 用单一 `query()`（`src/query.ts:276`）同时服务主线程与子代理（`AgentTool/runAgent.ts:776`），差异由 `createSubagentContext`（`src/utils/forkedAgent.ts:342`，「默认隔离全部可变状态，显式 opt-in 才共享」）提供；deepseek-harness 的根/子 agent 走同一 `ctx.agents.create()` 工厂（`subagent-in-process-driver/src/index.ts:134` vs `api/session-controller/src/agent.ts:477`），差异由 `applyChildComposition(persona, toolFilter)` 提供。LangChain 官方亦把 `create_agent` 定位为「构建 agent 的标准方式」，其 subagents 模式与 Deep Agents 的产品化实现都是「主/子都用 `create_agent`」。

**为什么现在做**：`skill-execution-and-delegation`（最高优先级）已把子代理侧彻底 `create_agent` 化，并接管子代理观测接线（其 D11：同 trace + 委派父 span + `scope=delegate` + 显式喂事件，`var_child_runnable_config` 的隔离保持不变）。子角色那一半已理顺，主图侧成为唯一的不一致来源。

**约束**：

- 本变更是**行为保持**的内部重构：对外契约（SSE 事件序列、citations、落库、前端）不变。
- 文件规模红线：`agent_node.py` 现 **391 行**，加 middleware 必超 400 ⇒ 必须新建落点文件。
- middleware 实例**跨请求共享**（随图构造一次）⇒ 禁止实例级 per-request 状态。
- 排序：`skill-execution-and-delegation` 在先（其 P2 会改写 `executor.py`）；`agent-round-budget` 在本变更之后。
- 素材与证据（不复述，按一事一档指向）：`docs/tmp/one-loop-two-roles-brief.md`（决策记录 + 探针结果）· `docs/tmp/deep-research-one-loop-two-roles.md`（官方一手引用 + 两参考实现源码行号 + 本仓探针）。

## Goals / Non-Goals

**Goals:**

- 主 agent 循环与 fork 子代理**出自同一装配入口**，差异仅由参数提供（对齐 claude-code / deepseek-harness）。
- 领域阶段（`verify` / `format` / `agent_finalize`）保持在外层图，位置与职责不变。
- 对外行为**逐字保持**：SSE 事件序列与 token 流、citations、`prompt assembled` / `prompt messages` / `iteration done` / `iteration limit` / `model turn` 五条日志的**事件名与字段集**、触顶路径的确切消息语义。
- 消除 regen 轮的预算复位管道（改由"每次 invoke 即新 run"自然承担）。

**Non-Goals:**

- 不改工具面（默认继承 + 只读收窄 + 禁用集）——归 `skill-execution-and-delegation` 的 D7/D8。
- 不改子代理的观测接线本体——归其 D11；本变更只"采用"。
- 不改轮次/时长护栏的**取值**与触顶兜底——归 `agent-round-budget`；本变更**不新增步数硬兜底**（今天本就没有）。
- 不改 `verify` / `format` 的判据与日志事件；不改 `skill_direct` 语义；不动 `skills/` 内容。
- 不删除 `timings` / `_token_usage` 两个已核实的死字段（仅登记，避免与本重构混在一起）。

## Decisions

### D1 L1 统一到 `create_agent`；L3 领域阶段留外层图

- 决策：新增装配入口 `build_agent(...)`（落在新文件 `src/agents/graph/agent_factory.py`），主/子角色共用；主图的 `agent` 节点改为「组装首轮消息 → invoke 该子图 → 回写」。`agent_finalize` / `verify` / `format` / `skill_direct` 仍是外层图节点（`route_verify` 回边目标名不变）。
- 理由：① 官方明确定位 `create_agent` 为标准方式；② `create_agent` 本身就是 `CompiledStateGraph`（已实测：节点 `model` / `tools`），切换成本可控；③ 领域阶段是本仓差异化资产且需要回边，塞进 middleware 无回边能力。
- 备选：**主图整换 `create_agent`（含 L3）** → 否决：要把 history 截断、注入抽取、温度分档、材料装载、verify 条件回边、`skill_direct` 入口全部重写，等于换底座同时重写产品逻辑；且 Anthropic 明确警示框架抽象会遮蔽底层 prompt/response、更难调试。
- 备选：**保留自建循环、只修观测** → 否决：观测已由对方 D11 接管，此路等于让"两套实现"长期共存。

### D2 system 的提供方式：节点一次组装、按类型拆两半；由 middleware 施加

- 决策：节点仍调 `build_prompt(...)`（保留该函数与其契约测试），产出**按类型拆分**——system 段写入**外层 `AgentState` 的声明字段**（新增，跨 invoke 持久），非 system 段（注入 + 历史 + 当前 user）作为子图输入；prompt middleware 每次模型调用从**该字段**取 system 段并施加。
- **载体必须在外层 state，不能在子图 state**：子图每次 `invoke` 结束即销毁，而**重生成轮**（`verify` 置 `_needs_regenerate` → `route_verify` 回 `agent`）时节点按"`state.messages` 非空即不组装"的既有语义**不再组装**（`agent_node.py:176-179`）。载体若在子图 state，重生成轮将拿不到任何 system 段（KB 未绑定的禁止检索约束、引用纪律、人设全部静默消失）。外层字段使重生成轮照常取到同一份 system，且**不触发第二次组装**。
- 施加用**自定义 `awrap_model_call`**（而非 `@dynamic_prompt` 糖）：因为未绑 KB 时需下发**两条** system 消息，而 `@dynamic_prompt` 只能设单条。**确切机制**：`request.override(system_message=<第一条>, messages=[<第二条>, *request.messages])` —— 模型节点把 `system_message` 放在最前、再接 `messages`，故实收 `[第一条, 第二条, ...]`，**顺序与今天一致**（`src/rag/prompt.py:248-250` 正是主 system 在前、未绑提示在后）。已在探针中验证该形态可达两条 system（当时的写法是 `create_agent(system_prompt=第一条)` + middleware 前插第二条）。
- 理由：`build_system_prompt` 每次生成仍只调一次 ⇒ `prompt assembled` 与 `prompt messages` 的产点与计数**零变化**，这是本决策最大红利。
- 备选：**middleware 内每请求组装**（`prompt_source` callable） → 否决：会变成每次 `invoke` 组装一次（首轮 + 每次重生成），`prompt assembled` 每生成多发 1~2 次且重复计算。
- 子角色的 system 为**静态串**（执行者人设 + 执行契约），直接用装配入口的静态参数，**不需要** prompt middleware——故装配入口的 system 参数有「静态串 / 经运行态携带」两形态。

### D3 回合上限改用自定义 middleware（不注入消息）

- 决策：`AgentTurnBudget` middleware，声明 `state_schema` 承载计数与委派标志；判定放 **`after_model`** 并用 `hook_config(can_jump_to=["end"])` 返回 `{"jump_to": "end"}`——**不注入任何消息**。
- **日志与 jump 解耦**：计数达到有效上限时**即**记 `iteration limit`（`query` / `iteration` 取自 state），**与"该轮是否仍声明工具调用"无关**——今天的产出条件正是如此（`agent_node.py:300-303` 的 `if iteration >= effective_max: log(...)`，不看 `tool_calls`）。若只在"有 `tool_calls` 才 jump"的分支里记日志，"上限轮恰好正常收尾"这一情形会少发一条告警。
- **委派放宽的语义（必须同时满足两条）**：`after_model` 检测本轮 `AIMessage` 是否含 `delegate_task` → **命中即置位**（置位先于本次上限判定），且该标志**跨轮保持**；有效上限 = `MAX_AGENT_ITERATIONS + MAX_DELEGATE_BONUS`。今天的行为正是 `delegate_used or state._delegate_used`（`agent_node.py:296`）——只做"本轮检测"会让第 N+1 轮的余量回落，只做"持久标志"而漏"先置位"会让**上限同轮声明的委派工具根本不执行**。
- 理由（三条均由探针定案）：① 官方 `ModelCallLimitMiddleware` 的 `exit_behavior="end"` 会**注入一条英文限流 `AIMessage`**，那会成为用户答案 ⇒ 不可用；② 判定若放 `before_model`，会**多执行一次工具**且末条变 `ToolMessage`，于是答案提取会把**工具结果当成答案**（用户可见倒退）；③ `after_model` 判定 + jump 实测精确复现今天的时点：模型调用 = 上限次、工具执行 = 上限−1 次、末条 = 含 `tool_calls` 的 `AIMessage`。
- 备选：工具钩子内 `Command(goto="__end__")` → 否决：实测**不终止循环**（模型反复产出工具调用，序列错乱）。

### D4 SSE 判别谓词小改

- 决策：`_convert_event` 里模型事件的判别由 `metadata.langgraph_node == "agent"` 改为 `== "model"`；**只由「节点判别键 + `scope == "main"`」两个既有维度组成**。同模块 docstring（`agent_service.py:186-208`）里残留的 `"agent"` 一并更正。
- **不引入 `checkpoint_ns` 兜底**：实测 `langgraph_node == "model"` 对主循环模型事件已充分（探针里 `on_chat_model_*` 无一为 `"agent"`）。加 `checkpoint_ns` 前缀匹配是**以更弱的证据放大匹配面**——一旦将来来源命名空间变化（例如不再切断子代理的回调继承），异命名空间的事件可能被误吸进主 SSE。域节点（`format` / `agent_finalize` / `skill_direct`）与 `ToolTraceCollector` 的 `== "tools"` **均不变**。
- 理由：实测——`create_agent` 作外层节点时，内层模型/工具事件带内层节点名（`model` / `tools`），外层节点名落在 `checkpoint_ns` 前缀；token 级流式（`on_chat_model_start/stream/end`）**仍然产出**。⇒ 这是**谓词级小改**，不是重写转换层。
- 正交性：事件管道域（`scope`）与节点判别是两个独立维度——`_convert_event` 现有 `if scope != "main": return`，本变更不改它。

### D5 `verify` / `format` 归位：外层图的「循环后校验与引用组装阶段」

- 决策：位置与形态**不动**（3a）。归属表述取中性——判据读**本轮材料池**（`_has_web_context` / `_has_kb_context` / 年份 / `[n]`），不由任何能力注册。
- 理由：① 两个入口（`agent_finalize` 与 `skill_direct`）都汇到 verify（`workflow.py:118/119`）⇒ 后阶段挂在**材料池**而非循环上；② `verify` 需要回边重生成，出图就要在 service 层自写重生成循环，更差；③ 把"有无接地产出"提到条件边（备选）不是新能力只是图更好看，且会砍掉 no-op 分支的既有可观测事件（`Event.SKIP reason=guard_pass`、`FORMAT_DONE reason=abstention/no_markers`）。

### D6 `agent_finalize` 不拆

- 决策：保持外层图**单个节点**，职责不变（通用：取末条 `AIMessage` 文本为答案；领域：把主 ctx 的 `tool_contexts` / `temporal_years` 快照进 state）。
- 理由：① 「通用出口 vs 领域装载」的拆分前提不成立——子角色**没有**这个节点（其产出聚合在 `fork_stream`，属对方改写面）；② 材料池快照进 state 是 `skill_direct` 路径的既有必需（否则直出轮判据空转）；③ 一个概念一个位置，且与对方的变更零交集。

### D7 消息回写策略：只回写子图**新增段**

- 决策：`agent` 节点返回 `{"messages": <子图输出中 seed 之后的部分>, "answer": <末条文本>}`。
- 理由：实测三种策略——`suffix`（新增段）与 `full`（整包）都干净，`last`（只末条）会丢 `ToolMessage` 与中间消息。`full` 之所以不重复，是因为 `create_agent` 把输入消息**按引用**带出（同 id，`add_messages` 按 id 去重）；`suffix` 不依赖该隐式前提，故取 `suffix`。
- regen 兼容：实测第二轮回写后消息序为 `[历史, 上轮答案, verify 指引(SystemMessage), 新答案]`，指引保留、历史不重复、模型确实看到指引 ⇒ 与今天 `state.messages` 的累积方式一致。

### D8 观测：采用对方 D11，不自建

- 决策：Langfuse 侧沿用 `skill-execution-and-delegation` 的 D11（同 trace、委派父 span、`scope=delegate`、`ToolTraceCollector` 参数化按缺省参数行为不变、显式喂事件、`set(None)` 保持不变）；本变更只把主 role 的 `agent_turn` generation span 从节点装饰器迁入 middleware。
- 理由：本仓 langfuse 为 **v2 SDK（2.60.10）**，`@observe` **没有** trace_id 参数 ⇒ 独立 trace 只能命令式、成本高一个量级；且 `trace_id` 是对外契约且正是 Langfuse 的 trace 主键 ⇒ 单 trace 嵌套与契约一致。对方的 `ToolTraceCollector` 新接口对缺省参数有回归测试，A 之后主域采集器**零改动**。

### D9 装配入口的落点与三条硬约束

- 决策：`build_agent` 落 `src/agents/graph/agent_factory.py`，middleware 三件套落 `src/agents/graph/middleware.py`（`agent_node.py` 因此缩到约 200 行）。
- 三条硬约束（写入 `agent-assembly` capability）：① 装配入口**不产出** `graph compiled` 日志（该事件仍由 `build_graph` 发一次，否则每委派多一条）；② middleware **不得**持有 per-request 实例状态（实例跨请求共享）；③ 回合上限由参数决定、委派轮放宽。
- 备选：middleware 内联进 `agent_node.py` → 否决：必超 400 行红线。

### D10 排序与 ADR 划分

- 决策：**一条 ADR**（`统一 agent 循环到 create_agent（L1/L2 参数化，领域阶段留外层图）`）。2b（system 施加）/ 3a（后阶段归位）/ finalize 不拆都是它的细节或推论，不单立；观测那条的决策权在对方 D11（重复写会造成两个 ADR 说同一件事）；七轴类契约进 spec 不进 ADR。编号**在对方写完之后取**（现最大 `0015`，其先占 `0016`）。
- 决策：本变更 apply 排在 `skill-execution-and-delegation` 之后；`agent-round-budget` 排在本变更之后。
- 理由：`docs/adr/README.md` 的「一条决策一文件」与并行分支编号会撞车的既有教训（`check_adr` 要求编号连续，重编号须与合并同一提交）。

### D11 装配后工具取数：沿用对方已落地的口径，只补两处

**背景**：工具经 `langgraph.prebuilt.InjectedState` 拿到的值，其形状由承载它的图决定——外层自建图给 `AgentState` 实例，`create_agent` 给 `dict`（实测两次：`state_schema=<dataclass>` 与默认 schema 都注入 dict）。主循环改由 `create_agent` 承载后，属性访问会 `AttributeError`，且被错误回喂吞成 `ToolMessage` 错误 ⇒ 检索恒空、澄清恒失败。

**对方已解决的部分（2026-09-28 落地，本变更不重复）**：`skill-execution-and-delegation` 在其 P2 中已修 `retrieve_kb`（提交 `8014d64`）：**`kb_id` 改为「注入的 AgentState 优先、`current_request_ctx.kb_id` 回退」**，并补了回归用例。其口径比"把字段搬进子图 state"更简——请求上下文本来就是这些字段的权威来源。⇒ 本变更**不要求**把 `kb_id` / `query` 重复 seed 进图状态。

**本变更仍需补的两处**：

1. **`ask_tools.py` 的 3 处取数**（`:84` `state.query` / `:85` `state._agent_iterations`、`:168-169` `state.kb_id`）。对方**有意未修**并留了注释：「若将来把它移出禁用集，须同 `retrieve_kb` 一样加 `isinstance(state, AgentState)` 守卫」——该判断**在它的范围内成立**（`ask_user` 在 `FORK_FORBIDDEN_TOOLS` 里，fork 子代理调不到，故其范围内不是活 bug）；但**主循环改由 `create_agent` 承载后，`ask_user` 就在 dict 状态下被调用** ⇒ 本变更必须处理。修法同既有口径（`isinstance` 分流 + `kb_id` 走 `ctx` 回退）。
2. **`retrieve_kb` 的迭代序号**：其现有守卫是 `if isinstance(state, AgentState): iteration = state._agent_iterations` ⇒ dict 状态下**恒取 0**，主循环的检索信号会**丢失真实轮次**。迭代序号在请求上下文里**没有对应项**，故这一项**必须由装配带入图状态**：dict 分支改读子图 schema 的 `_turn_count`（键名见 tasks 1.2），使主循环内工具仍上报真实序号。

**降级必须留痕**：字段缺失走降级时 SHALL 记 warning（含工具名与缺失字段名）。对方修法里 `kb_id` 回退失败会得到空串（静默），若再叠加"缺字段静默降级"，故障在日志上就完全不可见。

**备选**：让 `create_agent` 接受 dataclass 状态 → 实测不可行（其状态 schema 无条件生成 TypedDict）。备选：主循环不换 `create_agent`（评审提出的"参数化自建循环"）→ 见 D12。

### D12 备选「参数化自建循环」被评估并否决（评审提出，记录取舍）

评审提出第三条路：把现有自建 `agent ⇄ tools` 抽成共享工厂参数化，主图与子代理都用它——同样满足"一套循环两角色"，且**规避** D11 的工具形状问题、D4 的 SSE 谓词改动、middleware 的 `state_schema` 陷阱与 D2 的 system 施加通道问题。

**否决理由（三条均为评审未计入的账）**：

1. **与主规格直接冲突**：`delegate-task` 现明写「用 `langchain.agents.create_agent` 生成独立子代理」；让子代理迁离 `create_agent` 需再改该 requirement，并追加一条推翻性 ADR。
2. **与在途 change 对撞**：`skill-execution-and-delegation`（最高优先级、执行中）已把 `executor.py` 改成 `create_agent` 形态，其 D11 的观测接线也是**按 `create_agent` 的节点形状**设计并实测过的（它记录了「`create_agent` 产物同样有 `tools` 节点、自定义 config metadata 是合并而非覆盖」）。
3. **D11 的修复逃不掉**：对方 D7/D8 一落地，fork 子代理就会继承工具并踩同一形状问题 ⇒ 工具侧修复**本就在更高级别 change 的关键路径上**，不是本变更特有的成本。

⇒ 结论：备选省下的只是两处 middleware 陷阱 + 一处谓词小改，代价是推翻主规格并与在途 change 对撞，不划算。**放弃，理由记录于此**（满足评审要求"记录放弃理由"）。

## Risks / Trade-offs

- **[`delegate-task` 的 `fork 执行` requirement 与对方 delta 撞车]** → 我方该 delta 基于当前文本，同步会回退其工具面改动 ⇒ 该 delta 文件顶部已写明"归档前 MUST 重新复制其落地后的 requirement 全文"；tasks 中列为验收前置。
- **[工具经 `InjectedState` 取数在 `create_agent` 下崩溃]**（评审 Blocker，已实测复现） → 见 D11：`retrieve_kb` 的 `kb_id` 与其形状守卫**已由对方落地时修复**；本变更补 `ask_tools` 3 处 + `retrieve_kb` 的迭代序号 + 降级 warning。
- **[重生成轮的 system 段丢失]**（评审 Blocker） → 见 D2：载体放**外层 `AgentState` 的声明字段**（跨 invoke 持久），并断言"重生成轮仍含完整 system 段且不触发第二次组装"。
- **[交付顺序先于对方落地会导致"只有主角色被统一"]** → **已消解（2026-09-28）**：对方 P1+P2 已落地并归档 ⇒ 闸门满足；`agent-assembly` 的「不得存在第二套装配」此时是可直接达成的约束，不再需要"中间态"例外。
- **[`iteration limit` 的产出条件与今天不同]** → 见 D3：日志产出与 jump 判定**解耦**，"计数达上限即记"。
- **[委派放宽语义不完整会丢余量、甚至跳过委派]** → 见 D3：必须"本轮命中先置位 **OR** 此前已置位"两者兼有；补"上限同轮声明的委派仍被执行"断言。
- **[regen 预算复位是三处、`ctx.web_count` 是两处]** → `_agent_iterations` / `_delegate_used` 在 `guardrails.py:105/106`、`:180/181`、**`regen_decision.py:174/175`**（三处）；`ctx.web_count = 0` 在 `guardrails.py:99`、`regen_decision.py:169`（**两处**，与主循环预算无关故保留）。分别统一处置，并同步 `test_verify_node.py` 的多处返回值断言。
- **[删除 `state_schema` 未声明键的静默丢弃]**（探针连踩四次） → 见 `defensive-patterns.md` 的新增两条规则；tasks 中每条 middleware 都要有"计数/携带键已在 `state_schema` 声明"的断言。
- **[触顶空回答缺陷被无意改坏或被误认为已修]** → `agent-loop-observability` 的 ADDED requirement 把确切语义钉住，并要求断言（末条含 `tool_calls`、答案可为空）。
- **[SSE token 流断裂]** → 实测嵌套路径仍有 `on_chat_model_stream`，但**只验了事件形状、未验 token 内容** ⇒ 列为真实模型 E2E 验收项；回滚方式见 Migration。
- **[per-request 状态误存 middleware 实例属性 → 并发串号]** → `agent-assembly` 明确禁止；tasks 中列跨请求隔离断言。
- **[`recursion_limit` 语义误解]** → 已实测：不显式传 `recursion_limit` 时**没有步数上限**（`None` 与 `{}` 同理），今天生产即无图级硬兜底；嵌套时外层 limit 也管不到内层。本变更**不新增**该兜底，但在 spec/design 中写明该事实。
- **[观测接线重复实现]** → 明确采用对方 D11，本变更不新建采集器；`ToolTraceCollector` 走缺省参数（有回归测试护住）。
- **[验收不足以证明"对外行为不变"]**（评审 Important） → 补 4 项断言：取消/断连穿过子图、`create_agent` 内工具取数、五条日志的**产出次数**、`capture.model_used` 捕获；并点名 `SKIP` / `FORMAT_DONE` / `skill_direct` 直出轮的事件仍产出。

## Migration Plan

- **顺序**：① `skill-execution-and-delegation` 的 P1 与 P2 **已落地并归档**（2026-09-28） ⇒ 前置满足 → ② 本变更（装配入口 + middleware + `agent` 节点 + SSE 谓词 + 工具取数补两处 + 字段清理 → 子角色接入） → ③ `agent-round-budget`。
- **闸门（原收口 (a)）**：曾规定"对方 P2 未落地则不开工"，**该条件已满足**——本变更可随时开工；`agent-assembly` 的「不得存在第二套装配」须在本变更内直接达成（不再有"中间态"）。
- **无 DB 迁移、无 API 破坏**；`delegate_task` 参数契约不变。
- **回滚**：恢复 `agent` 节点为原 `make_agent_model_node` / `make_agent_tools_node` / `route_agent`，恢复 SSE 判别谓词为 `"agent"`，恢复 `AgentState` 三个字段与 regen 复位；装配入口与 middleware 可整体弃用（新文件，删除即回滚）。工具侧的改动**不回滚**（`ask_tools` 的形状判定向后兼容两种承载；`retrieve_kb` 的迭代序号读取在主图承载下仍需保留）。无数据面回滚。
- **验收前置**：本变更的 `delegate-task` delta 须 rebase 到对方落地后的主规格文本。

## Open Questions

- `agent_turn` generation span 迁入 middleware 是否成立（`langfuse_context.update_current_observation` 在 `awrap_model_call` 中是否指向正确 observation）——实现期验证，失败则回退为命令式 span。
- 通用委派的角色 `name` 取值（主角色取 `"main"`；子角色取 skill 名；通用委派待定）——**以 `skill-execution-and-delegation` 的 D6 结论为准**。
- 取消/断连穿过"节点内 `await` 子图"的传播：机制上预期一致（外层事件循环仍逐事件检查 `abort_signal`），但**必须在单元层覆盖**，不能只靠真实 E2E（评审 Important）。
- **已收口的原问题**：① "子角色是否必须在本变更内接入同一装配入口"→ 取 **(a) 必须**，P2 未落地则不开工（见 Migration 的闸门）；② "是否评估参数化自建循环"→ 已评估并否决，理由见 D12。
- 备选记录（供后人复查）：评审提出的第三条路（参数化自建循环）虽被否决，但若将来 `delegate-task` 要求变动或对方 P1/P2 方向调整，**D12 的三条理由需要重新核对**——尤其第 3 条（工具形状修复是否真的独立于本变更）。
