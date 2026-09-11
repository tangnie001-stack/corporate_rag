## MODIFIED Requirements

### Requirement: fork 执行

系统 SHALL 让 context=fork 的 skill 走子代理路径。

#### Scenario: 生成子代理

- **WHEN** delegate_task 命中 fork skill
- **THEN** 用 `langchain.agents.create_agent` 生成独立子代理（**不使用已废弃的 `langgraph.prebuilt.create_react_agent`**）
- **AND** 子代理 system_prompt = 执行者人设（优先级：skill 的 `agent:` 声明 > 本会话选定智能体 > 系统默认 prompt）
- **AND** 子代理初始 user message = skill 内容（任务/方法论）；由 `/xxx` 触发时，`/` 后的剩余文本作为输入
- **AND** 子代理工具集 = 执行者预设 tools ∩ skill 的 allowed-tools（allowed-tools 为空则继承执行者预设 tools；均为空则零工具）
- **AND** 子代理使用独立 RequestContext（独立 tool_contexts / 引用编号 / pending_asks），不污染主 agent
- **AND** 子代理最大轮次取执行者预设的 `maxTurns`（未声明则用系统默认上限）
- **AND** `create_agent` 的 middleware 参数保留装配位但默认传空（v1 不启用）

#### Scenario: fork 执行者的选择顺序

- **WHEN** 一个 fork skill 被执行
- **THEN** 执行者按优先级：skill 的 `agent:` 声明 > 本会话选定智能体 > 系统默认 prompt

#### Scenario: 模型覆盖

- **WHEN** skill frontmatter 声明了 model
- **THEN** fork 子代理用 `get_llm(model=record.model)` 新建实例（仅 fork 生效）
- **WHEN** skill 未声明 model
- **THEN** 复用主 agent 的 llm 实例

### Requirement: 材料由主 agent 预检索

当 fork 子代理无检索工具时，主 agent SHALL 在 delegate 前先检索好，把 context 作为 task 一部分传入。**该预检索只适用于"模型自动委派"路径**（主 agent 已跑过一轮、手上有上下文）。

#### Scenario: 无检索工具时预检索

- **WHEN** fork 子代理工具集不含 retrieve_kb/search_web（模型自动委派路径）
- **THEN** 主 agent 在 delegate 前完成检索，材料全凭 task 携带

#### Scenario: 有检索工具时自行检索

- **WHEN** fork skill 的 allowed-tools 含 retrieve_kb/search_web
- **THEN** 子代理可在独立 RequestContext 内自行检索
- **AND** 子代理正文可正常引用其工具
- **AND** 在**模型自动委派**路径下，其结果不计入主 agent 的引用编号（主 agent 之后自己写答案、自己引用）；**`/xxx` 触发 fork 直出**路径的引用池归属见下一条 Requirement

#### Scenario: `/xxx` 触发的 fork 不做预检索

- **WHEN** 用户用 `/xxx` 触发一个 `context: fork` skill（主 agent 零 LLM 轮，见 `agent-service`）
- **THEN** 不执行"主 agent 预检索"（主 agent 本轮无上下文可预检索）
- **AND** 该 skill 的 `allowed-tools` 应包含所需检索工具；若既无检索工具又无外部材料，子代理仅凭 skill 正文与用户输入作答，并在结果中如实说明证据不足

## ADDED Requirements

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
- **AND** 重跑子代理总次数不超过 1 次

#### Scenario: 子代理工具集无交互工具

- **WHEN** 检查 fork 子代理的工具集
- **THEN** 不含 `ask_user` / `ask_confirm`（需要交互的 skill 应改用 `context: inline`）

### Requirement: fork 直出路径的引用池归属

`/xxx` 触发的 fork **直出**（主 agent 零 LLM 轮，见 `agent-service`）时，本轮 citations SHALL **以子代理的 `tool_contexts` 为引用池**（其编号与子代理答案中的 `[n]` 天然一一对应），交由 `format` 节点组装来源。

理由：该路径下主 agent **从未调用检索工具**，主引用池必为空；若沿用主池，`format` 会把子代理答案里的全部 `[n]` 判为越界丢弃（`citations: []`），答案看起来有引用却查不到来源，同时误记 `INVALID_CITATION` 信号；而两条引用护栏均以"主池有 context"为前提，会**静默放行**、无任何告警。

#### Scenario: 直出路径产出引用

- **WHEN** 用户 `/xxx` 触发一个 `context: fork` skill，子代理检索到 2 条 KB 结果并在答案中标注 `[1][2]`
- **THEN** 该轮 `citations` 非空，`index` 为 1、2，`source`/`page`/`snippet`/`tier` 取自子代理引用池
- **AND** 前端来源横条与引用抽屉正常渲染

#### Scenario: 不产生幻觉引用误报

- **WHEN** 直出路径的答案标注了合法范围内的 `[n]`
- **THEN** 不产生 `INVALID_CITATION` 信号（不把合法引用统计为模型幻觉编号）

#### Scenario: 主 agent 自动委派路径不受影响

- **WHEN** 主 agent 自动委派（非 `/xxx`）并在随后自己写出答案
- **THEN** citations 仍由其自身引用池组装（子代理检索不计入主编号），行为与既有一致
