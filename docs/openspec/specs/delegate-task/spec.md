# delegate-task Specification

## Purpose
TBD - created by archiving change agent-delegation-skills. Update Purpose after archive.
## Requirements
### Requirement: delegate_task 工具

系统 SHALL 在主 agent 工具集新增 `delegate_task(task, skill?)` 工具；`skill` 为**可选**参数。

传入 `skill` 时为**定点委派**（既有语义）；**省略 `skill` 时为通用委派**——不加载任何 skill 正文，用执行者人设 + `task` 描述构建子代理。两条路径 SHALL 共用同一套控制：隔离 RequestContext、独立引用池、`maxTurns` 上限、总时长兜底、取消传播、`delegate_id` 事件。

#### Scenario: 调用入口

- **WHEN** 主 agent 判断任务需领域能力
- **THEN** 调 delegate_task 并传入 task（任务描述）与 skill（要用的 skill 名）
- **AND** 工具的 description 动态列出可用 skill（名 + whenToUse，预算截断）

#### Scenario: 通用委派（省略 skill）

- **WHEN** 主 agent 判断某个深度任务需要独立子代理，但不对应任何已注册 skill
- **THEN** 调 delegate_task 只传 task，系统不加载 skill 正文
- **AND** 执行者人设按"本会话选定智能体 > 系统默认"选择（无 skill 的 `agent:` 可参与）
- **AND** 子代理走与 fork 相同的执行路径与兜底（工具面、隔离、超时、轮次上限、引用池归属）
- **AND** 该次委派事件与任务看板条目标记为通用委派（与定点委派可区分）

#### Scenario: 未知 skill

- **WHEN** 请求的 skill 不在注册表
- **THEN** 返回"skill 不存在" + 可用列表

### Requirement: inline 执行
系统 SHALL 让 context=inline 的 skill 走内联路径。

#### Scenario: 返回指引

- **WHEN** delegate_task 命中 inline skill
- **THEN** 返回 skill 的 inline_prompt（task 填入 {task} 占位）
- **AND** 主 agent 收到指引后自行执行（不产生独立子代理）

### Requirement: fork 执行

系统 SHALL 让 context=fork 的 skill 走子代理路径。

#### Scenario: 生成子代理

- **WHEN** delegate_task 命中 fork skill
- **THEN** 用 `langchain.agents.create_agent` 生成独立子代理（**不使用已废弃的 `langgraph.prebuilt.create_react_agent`**）
- **AND** 子代理 system_prompt = 执行者人设（优先级：skill 的 `agent:` 声明 > 本会话选定智能体 > 系统默认 prompt）
- **AND** 子代理初始 user message = skill 内容（任务/方法论）；由 `/xxx` 触发时，`/` 后的剩余文本作为输入
- **AND** 子代理工具面 = **本轮主 agent 启用工具集** − 禁用集（`FORK_FORBIDDEN_TOOLS` + 主 agent 专属工具类）− **非只读工具**（依 `readonly_map()` 判定；表中缺项按非只读处理，不下发）∩（skill 声明 `allowed-tools` 时）allowed-tools ∩（执行者预设声明 `tools` 时）preset.tools
- **AND** `allowed-tools` 的语义是**收窄项**：不声明即不收窄（但仍受禁用集与只读约束），SHALL NOT 因未声明而退化为零工具；**显式声明可放行非只读工具**（白名单退为例外通道）
- **AND** 子代理使用独立 RequestContext（独立的 `tool_contexts` / 引用编号；`pending_asks` 为进程级按 session 的单槽、**不随子上下文复制**），不污染主 agent
- **AND** 子代理最大轮次取执行者预设的 `maxTurns`（未声明则用系统默认上限）
- **AND** `create_agent` 的 middleware 参数保留装配位但默认传空（v1 不启用）

#### Scenario: 工具面缺省为继承

- **WHEN** 一个 fork skill 未声明 `allowed-tools`，执行者预设也未声明 `tools`
- **THEN** 子代理拿到与主 agent 相同的**只读**工具面（减去禁用集与非只读工具），可自主检索
- **AND** 不产生"零工具告警"（该告警的语义已作废）

#### Scenario: 非只读工具默认不下发

- **WHEN** 主 agent 工具面中存在非只读工具（依 `readonly_map()`；表中**缺项**按非只读处理），且 skill 未在 `allowed-tools` 中显式声明它
- **THEN** 该工具不出现在子代理工具面
- **AND** 理由：子代理（尤其 `/xxx` 直出，其调用早于任何用户确认）不得自动获得写权限；写权限必须由 skill 显式声明

#### Scenario: 只读表为空时的极性（与双轴推导不同，须写明）

- **WHEN** 装配 fork 工具面时 `readonly_map()` 为空（工具尚未注册）
- **THEN** 按 **fail-closed** 处理（不下发任何工具）并记 warning——与 `derive_invocation_flags` 对空表的 **fail-open**（`src/agents/skills/invocation.py:33-36`）**极性相反**
- **AND** 该差异是**有意为之**：同一张表的两个消费者失败代价不同——双轴推导空表时不锁只是少了一层保护，而 fork 侧空表时"按只读放行"会把写权限下发给子代理。实施者不得为"统一"而改掉任一极
- **AND** 生产运行期该表必已填充（工具在 graph 构建期注册），空表只应出现在启动早期或测试替身

#### Scenario: 声明收窄

- **WHEN** skill 声明 `allowed-tools: [retrieve_kb]`
- **THEN** 子代理工具面被收窄为该清单（仍先减禁用集、再与执行者预设 `tools` 取交集）

#### Scenario: 新工具自动流入

- **WHEN** 工具注册表新增一个**只读**工具（含 MCP 只读工具）
- **THEN** 该工具**自动**进入子代理工具面，无需修改任何 skill 声明
- **WHEN** 新增的是非只读工具
- **THEN** 仅在某个 skill 的 `allowed-tools` 显式声明后才下发到该 skill 的子代理

#### Scenario: fork 执行者的选择顺序

- **WHEN** 一个 fork skill 被执行
- **THEN** 执行者按优先级：skill 的 `agent:` 声明 > 本会话选定智能体 > 系统默认 prompt

#### Scenario: 模型覆盖

- **WHEN** skill frontmatter 声明了 model
- **THEN** fork 子代理用 `get_llm(model=record.model)` 新建实例（仅 fork 生效）
- **WHEN** skill 未声明 model
- **THEN** 复用主 agent 的 llm 实例

### Requirement: 材料由主 agent 预检索

材料获取的归属 SHALL 按子代理工具面判定：

- 子代理工具面**含检索工具**时，SHALL NOT 要求主 agent 预先检索——材料由子代理自行获取；主 agent 可选择性把已到手的材料随 `task` 传入，以避免子代理重复检索。
- 子代理工具面**不含检索工具**时（执行者预设显式收窄所致），主 agent SHALL 在委派前完成检索并把结果作为 `task` 一部分传入。

#### Scenario: 子代理有检索工具时不要求预检索

- **WHEN** 子代理工具面含 `retrieve_kb`
- **THEN** 主 agent 直接委派即合法；子代理自行检索以补足事实
- **AND** 子代理的检索落在**子**引用池、不回流主池；子代理**不标注 `[n]`**（`[n]` 由主 agent 按其自身引用池补标）
- **AND** 委派路径的引用因此**完全来自主 agent 自身的检索**——要形成可引用来源，主 agent 须自行检索，或把材料随 `task` 传入

#### Scenario: 主 agent 顺带传材料

- **WHEN** 主 agent 已在本轮检索到相关材料再委派
- **THEN** 材料随 `task` 一并传入，子代理可复用而不必重复检索

#### Scenario: 直出路径的引用策略不同

- **WHEN** `/xxx` 直出触发的子代理自检索
- **THEN** 子代理**须标注 `[n]`**，其子引用池即本轮引用池（该路径无主 agent 补标）
- **AND** 两条路径的 `[n]` 策略不同是**有意为之**，实施者不得"统一"它们；按路径给子代理不同的指示（`FORK_DEFAULT_EXECUTOR_PROMPT` 现无条件要求"不标注 `[n]`"，在直出路径须追加相反指示）

#### Scenario: 无检索工具时预检索

- **WHEN** 执行者预设收窄后子代理无检索工具
- **THEN** 主 agent SHALL 在委派前检索并把材料并入 `task`

### Requirement: 结果回流
系统 SHALL 将 fork 子代理结果回传给主 agent。

#### Scenario: 纯文本返回

- **WHEN** fork 子代理完成
- **THEN** 返回其最终文本（不带引用编号 [n]）
- **AND** 主 agent 用自己的 tool_contexts/引用体系重组后走 verify → format
- **AND** 主 agent 重组时的引用仅指向其自身 tool_contexts 中的来源；子代理分析文本不写入 tool_contexts，不作为可引用来源（format 不会为子代理分析配 citation）

#### Scenario: 专家分析观点豁免

- **WHEN** 主 agent 整合的纯分析型答案（零 [n]、无检索依据、含 EXPERT_ANALYSIS_MARKER 措辞）
- **THEN** 态 B kb_citation_guardrail 不强灌补标 regen（专家分析观点豁免，design D9）
- **AND** 检索事实陈述仍须带 [n]（豁免仅覆盖无源专家观点，防模型绕过溯源）

#### Scenario: 结果截断

- **WHEN** fork 子代理返回超过阈值（~1000 字）
- **THEN** delegate_task 返回截断摘要（含总字数提示），防主 agent 上下文膨胀

### Requirement: 超时兜底
系统 SHALL 为 fork 执行提供超时保护。

#### Scenario: 超时终止

- **WHEN** fork 子代理执行超过 DELEGATE_TIMEOUT
- **THEN** asyncio.wait_for 终止执行，返回超时错误提示

### Requirement: 防递归

系统 SHALL 禁止子代理再次调用 `delegate_task`，并禁止子代理触碰主 agent 专属工具。

#### Scenario: 禁用集硬保证

- **WHEN** 构造 fork 子代理工具面
- **THEN** 结果恒不含 `FORK_FORBIDDEN_TOOLS`（`ask_user` / `delegate_task`）与主 agent 专属工具类（`task_*`）
- **AND** 即使 skill 的 `allowed-tools` 显式声明这些名字，也不下发（禁用集优先于白名单）
- **AND** 防递归由**禁用集**保证，不再依赖"工具面恒空"

#### Scenario: 子代理不直接与用户交互

- **WHEN** 子代理需要向用户提问
- **THEN** 它拿不到 `ask_user`；需要中途交互时由编排层确认门承载（一问一答 + 带答复重跑一次），不改用 fork 之外的其他执行路径

### Requirement: delegate 并发安全

系统 SHALL 支持一轮内多个 `delegate_task` 调用**并发执行**（ToolNode 对一轮内多个 tool_call 以 `asyncio.gather` 并发调度）。委派状态 SHALL 按 `delegate_id` 分槽隔离，不得使用 RequestContext 单值字段承载活跃委派状态。

#### Scenario: 一轮多委派并发

- **WHEN** 主 agent 在一轮内发起 3 个 `delegate_task` 调用
- **THEN** 三者并发执行（非串行排队）
- **AND** 各自的 `delegate_id` / `fork_stop_reason` / SSE 事件 / 任务看板条目互不覆盖

#### Scenario: 增量事件按 id 归属

- **WHEN** 两个委派并发产生 thinking/content 增量
- **THEN** 每个增量事件携带其 `delegate_id`，前端按 id 分节渲染，不串号

#### Scenario: 并发下的取消

- **WHEN** 请求被取消（abort_signal 置位）且存在多个在途委派
- **THEN** 所有在途委派各自以 `cancelled` 终态收尾

### Requirement: 子代理不直接交互与确认门

fork 子代理 SHALL NOT 持有面向用户的交互工具（`ask_user` / `ask_confirm`）。子代理需要用户确认时 SHALL 返回确认请求；**编排层确认门**（规则判断，不调 LLM）SHALL 检测该请求并经 `ask_user` 询问用户，用户答复后 SHALL **重跑子代理**（上限 1 次）。

#### Scenario: 子代理请求确认

- **WHEN** fork 子代理返回"需要用户确认"信号
- **THEN** 确认门经现有澄清链路（`ask_user` / `clarify_channel`）向用户提问
- **AND** 用户答复后带答案重新委派该 skill

#### Scenario: 无确认信号直通校验

- **WHEN** fork 子代理返回内容不含确认请求
- **THEN** 直接进入 verify（编排层校验），不触发 ask_user

#### Scenario: 用户拒绝或超时

- **WHEN** 用户拒绝确认、或询问超时、或澄清槽已被占用
- **THEN** 子代理基于现有信息给出结论，并在回答中显式标注"未经确认"
- **AND** 本轮**不再进入 verify 重跑**（与 verify 重跑预算互斥，故每轮最多重跑 1 次）

#### Scenario: 子代理工具集无交互工具

- **WHEN** 检查 fork 子代理的工具集
- **THEN** 不含 `ask_user` / `ask_confirm`（需要交互的 skill 应改用 `context: inline`）

### Requirement: fork 直出路径的引用池归属

`/xxx` 触发的 fork **直出**（主 agent 零 LLM 轮，见 `agent-service`）时，本轮 citations SHALL **以子代理的 `tool_contexts` 为引用池**（其编号与子代理答案中的 `[n]` 天然一一对应），交由 `format` 节点组装来源。

理由：该路径下主 agent **从未调用检索工具**，主引用池必为空；若沿用主池，`format` 会把子代理答案里的全部 `[n]` 判为越界丢弃（`citations: []`），答案看起来有引用却查不到来源，同时误记 `INVALID_CITATION` 信号；而两条引用护栏均以"主池有 context"为前提，会**静默放行**、无任何告警。

直出路径**不需要额外的准入守卫**：它唯一的结构性缺口（子代理不持有 `ask_user`）已由**既有确认门**承载——子代理按 `FORK_EXECUTION_CONTRACT` 输出确认标记，确认门规则检测后经澄清链路问用户并带答复重跑一次（见本 capability「子代理不直接交互与确认门」）。故直出对"需要中途交互的 skill"仍成立，只是中途交互能力为**一次**。

#### Scenario: 直出路径产出引用

- **WHEN** 用户 `/xxx` 触发一个 `context: fork` skill，子代理检索到 2 条 KB 结果并在答案中标注 `[1][2]`
- **THEN** 该轮 `citations` 非空，`index` 为 1、2，`source`/`page`/`snippet`/`tier` 取自子代理引用池
- **AND** 前端来源横条与引用抽屉正常渲染

#### Scenario: 子代理无检索工具时直出引用为空

- **WHEN** 某 fork skill 被显式收窄为无检索工具，直出轮子代理未产生检索结果
- **THEN** 该轮 `citations` 为空，不产生 `INVALID_CITATION` 信号

#### Scenario: 不产生幻觉引用误报

- **WHEN** 直出路径的答案标注了合法范围内的 `[n]`
- **THEN** 不产生 `INVALID_CITATION` 信号（不把合法引用统计为模型幻觉编号）

#### Scenario: 主 agent 自动委派路径不受影响

- **WHEN** 主 agent 自动委派（非 `/xxx`）并在随后自己写出答案
- **THEN** citations 仍由其自身引用池组装（子代理检索不计入主编号），行为与既有一致

### Requirement: 模型裁量委派的会话级预算

系统 SHALL 对**模型裁量的委派**（主 agent 调用 `delegate_task`，含定点与通用）实施**会话级次数上限**，默认值取自 `settings.DELEGATE_MAX_PER_SESSION`。

用户显式 `/xxx` 触发的 fork 直出 SHALL NOT 消耗该预算——其调用量由用户与"每轮至多一次"决定，属可预期调用。

超限时系统 SHALL 拒绝该次委派并返回**可读原因**（提示改用自身能力完成本轮），SHALL NOT 抛异常、SHALL NOT 中断本轮生成；并记 `delegate skip`（`reason=budget_exhausted`）。

计数 SHALL 按会话隔离并在会话清理时重置。

系统 SHALL NOT 设置委派嵌套深度上限——子代理不持有 `delegate_task`（由禁用集保证），委派深度恒为 1。

#### Scenario: 超限拒绝

- **WHEN** 同一会话内模型裁量的委派次数已达上限，主 agent 再次调用 `delegate_task`
- **THEN** 该次委派被拒绝，工具返回可读原因并提示改用自身能力
- **AND** 本轮生成继续，不抛异常
- **AND** 落 `delegate skip`（`reason=budget_exhausted`）

#### Scenario: 显式直出不消耗预算

- **WHEN** 用户以 `/xxx` 触发 fork skill 直出
- **THEN** 该次 fork 执行不计入会话委派预算
- **AND** 即使模型裁量委派已达上限，用户仍能用 `/xxx` 触发直出

#### Scenario: 会话隔离与重置

- **WHEN** 另一会话开始，或本会话被清理
- **THEN** 委派计数独立/归零，不与其他会话共享

#### Scenario: 委派深度恒为 1

- **WHEN** 子代理运行中试图再次委派
- **THEN** 它拿不到 `delegate_task`（禁用集），无法产生第二层委派
- **AND** 因此不需要、也不设置嵌套深度上限

### Requirement: 子代理确认标记在委派路径不泄漏

fork 子代理的"需确认"标记（`CONFIRM_REQUIRED:` 行）是**内部协议串**，SHALL NOT 出现在任何用户可见或主 agent 可见的文本中。

在**主 agent 委派路径**（`delegate_task`），子代理返回文本回到主 agent 之前 SHALL 剥离该**协议标记前缀**，但 SHALL **保留标记行上的问题文本**（转为普通文本行）：主 agent 需要知道"子代理在等什么确认"才能决定是否向用户提问。SHALL NOT 整行丢弃。

直出路径沿用既有确认门处理（检测 → 提问 → 重跑 / 未确认标注）。

理由：该标记此前只在直出路径被剥离（`strip_confirm_marker` 的唯一调用点在 `skill_direct`），委派路径会把它原样作为工具结果交给主 agent——协议串泄漏，且主 agent 会把它当正文读。**但既有的 `strip_confirm_marker` 是整行删除**（`confirm_gate.py:37-44`），而问题文本就在那一行上；若委派路径直接复用它，"子代理在等确认"这件事会被**静默吞掉**，主 agent 拿到一个无提示的不完整答案。故委派路径需要"剥前缀、留文本"的处理，而非直接复用整行删除。

#### Scenario: 委派路径剥离标记但保留问题

- **WHEN** 主 agent 委派的 fork 子代理返回文本含 `CONFIRM_REQUIRED: 请提供公司代码` 行
- **THEN** 返回给主 agent 的工具结果不含协议前缀 `CONFIRM_REQUIRED:`
- **AND** `请提供公司代码` 这层信息**保留**（主 agent 据此自行决定是否向用户提问）
- **AND** 不得整行删除导致该信息丢失

#### Scenario: 直出路径行为不变

- **WHEN** `/xxx` 直出的子代理返回文本含该标记
- **THEN** 走既有确认门（检测 → 提问 → 带答复重跑一次 / 被拒或超时则出结论并标注"未经确认"）

### Requirement: 委派路径的子上下文隔离与并发分槽

主 agent 委派（定点与通用）SHALL 与直出路径一致地为**每次委派**建立独占的子上下文（`DelegateRun` + `RequestContext.child()`），并把该运行态传入执行器。子代理的检索结果与引用编号 SHALL 写入**子**引用池，SHALL NOT 污染主 agent 的引用池。

委派状态（`delegate_id`、停止原因等）SHALL 写在该次委派的**子上下文**（`run.ctx`）上——每个委派各有自己的子上下文，天然按委派分槽；SHALL NOT 把委派状态写在**主上下文**上。

**豁免**：当不存在请求上下文时（`delegate_task` 的 `current_request_ctx.get() is None` 分支）没有父上下文可派生，隔离要求不适用，系统 SHALL 按既有 fail-open 处理并记 warning，SHALL NOT 因此抛错。

理由：现状 `delegate_task` 的三处调用均不传运行态（`src/agents/skills/delegate_task.py:90,94,135`），执行器因此走"用主 ctx、不隔离"分支（`src/agents/skills/executor.py:177-179`）。子代理零工具时该缺陷不可见；一旦子代理获得工具面，其检索会写进主引用池，导致主 agent 答案中的 `[n]` 与 `citations` 错配，并违反本 capability「fork 执行」与「delegate 并发安全」两条既有要求。此隔离是"子代理获得工具面"的**前置条件**。

#### Scenario: 委派路径的子代理检索不污染主池

- **WHEN** 主 agent 调用 `delegate_task`（定点或通用），子代理执行 `retrieve_kb` 检索到 2 条结果
- **THEN** 主 agent 的引用池保持为空（该次检索记入子池）
- **AND** 主 agent 随后自行检索并写出答案时，其 `[n]` 编号从 1 开始、与自身引用池一一对应

#### Scenario: 一轮多次委派互不串号

- **WHEN** 同一轮内主 agent 发起两个 `delegate_task`
- **THEN** 两次委派各有独立的 `delegate_id` 与子上下文
- **AND** 任一次的中断原因不写入对方的运行态

#### Scenario: 直出路径行为不变

- **WHEN** `/xxx` 命中的 fork skill 直出
- **THEN** 仍以其子代理的引用池组装本轮 citations（既有行为）
