## ADDED Requirements

### Requirement: 会话智能体人设注入

AgentService SHALL 接收流式请求的 `agent` 字段（智能体预设 `name`，可空）。生效值非空时按名解析 AgentPreset，将其正文作为本会话主 agent 的 system prompt 人设层（组装规则见 `prompt-composition`）；预设声明的 `skills` SHALL 在该会话**首轮生成前注入一次**（隐藏消息，与 `/xxx` 同一条注入路径，不进 system prompt）。生效值为空时 SHALL 使用系统默认 system prompt（既有行为不变）。

#### Scenario: 请求携带 agent

- **WHEN** 流式请求 body 含 `agent="finance-expert"`
- **THEN** 本轮生成以该预设正文作为主 agent 的 system prompt 人设层

#### Scenario: 预绑定 skill 预加载

- **WHEN** 生效预设声明 `skills: [财报分析手册]` 且当前为该会话首轮
- **THEN** 该 skill 正文以隐藏消息注入会话上下文（不进 system prompt）
- **AND** 后续轮次持续可见，且**不重复注入**

#### Scenario: 未知 agent 降级

- **WHEN** 生效值在注册表中不存在
- **THEN** 降级为系统默认 system prompt，并记 warning（不报错、不中断）

#### Scenario: agent 为空

- **WHEN** 请求未携带 agent
- **THEN** 使用系统默认 system prompt，行为与既有一致

### Requirement: 会话智能体的绑定与沿用（bind-once）

系统 SHALL 在**首次**收到非空且已注册的 `agent` 时，将其绑定到该会话（`bind-if-empty` 原子更新，只写一次）；此后每轮 SHALL 以**会话已绑定值**为唯一生效值：

- 已绑定 + 传入为空 → 静默沿用已绑定值（正常：刷新 / 旧客户端）
- 已绑定 + 传入非空且不同 → **忽略传入值**，按已绑定值继续本轮，**记 warning**（不拒绝、不阻断）
- 传入未注册 → 忽略该值 + warning；不影响已有绑定

请求 `agent` 的校验 SHALL 只依赖注册表，**不依赖 `catalog.json`**。绑定校验与写入 SHALL 在流式响应开始前完成（流一旦开始，HTTP 状态已发出，无法再表达业务拒绝）。

#### Scenario: 首轮绑定

- **WHEN** 新会话首轮携带 `agent="finance-expert"`
- **THEN** 会话绑定为 `finance-expert`，本轮以其人设生成

#### Scenario: 之后每轮沿用

- **WHEN** 会话已绑定 `finance-expert`，后续请求携带同一值、或携带空值
- **THEN** 一律按 `finance-expert` 生成（空值静默沿用）

#### Scenario: 绑定后传入不同值（忽略 + 报警，不阻断）

- **WHEN** 会话已绑定 `finance-expert`，新请求携带 `agent="tax-expert"`
- **THEN** 本轮仍按 `finance-expert` 生成（**不拒绝、不中断**）
- **AND** 记 warning（`bound` / `requested` 字段），且绑定值不变

#### Scenario: 未注册值不改变绑定

- **WHEN** 请求携带未注册的 agent 名
- **THEN** 忽略该值并记 warning；会话未绑定时降级为系统默认 prompt

#### Scenario: 上线前已存在的会话可绑定

- **WHEN** 会话行已存在且 `agent` 为空，请求携带合法 agent
- **THEN** 绑定成功（`bind-if-empty`），不受 `create_session` 幂等跳过的影响

#### Scenario: 预设被删除 / 改名后的历史会话

- **WHEN** 打开的历史会话绑定的预设名在注册表中已不存在
- **THEN** 记 warning，按系统默认 prompt 生成（不报 500、不清空会话）
- **AND** 前端回显该原始名（降级展示）

#### Scenario: 生效值回传

- **WHEN** 本轮生成完成（含"传入值被忽略"的情形）
- **THEN** 流事件携带 `agent_used`（本轮实际生效的智能体名），供前端纠正显示

### Requirement: `/xxx` 前缀在服务层的路由与执行形态

AgentService SHALL 在生成入口解析用户消息的 `/name` 前缀：命中 skill 则将其正文注入会话上下文（隐藏消息）并按其 `context` 执行，**跳过"是否委派"的模型判断**；注入内容 SHALL 保留在会话上下文中供后续轮次使用（触发粒度＝消息级，生效范围＝会话级）。

**执行形态**：

- `context: inline` → 主 agent 携带注入内容**单轮**作答
- `context: fork` → **直接调用 fork 子代理**产出结果，主 agent **不先产生 LLM 轮**；子代理结果 SHALL 交由主图 `verify` 节点校验（规则判断，无 LLM 调用），不通过时最多重跑子代理 **1 次**

**未命中判定**：

- `/` 开头且后续 token 形如 skill 名（`^/[A-Za-z0-9][\w-]*`）但未注册 → 返回"skill 不存在 + 可用列表"，**不静默按普通文本处理**
- 不以 `/` 开头、或 `/` 后不构成命令形态 → 按普通文本处理

**前缀的存储与清洗**：用户消息 SHALL 以**原文落库**（保留 `/name` 前缀）；组装 prompt 时（当前轮 `query` 与历史 user 消息）SHALL **剥掉 `/name ` 前缀**后再转 `HumanMessage`。清洗规则 SHALL 与 `/xxx` 解析**共用同一函数**（一处事实来源）。

理由：不剥离会让模型每轮都在历史里看到斜杠命令（可能模仿该格式、或把它当路径复述），并与注入的隐藏消息重复（同一 skill 出现"命令 + 正文"两次）；若改为"剥离后落库"，则用户输入与存储不一致，且剩余文本为空时撞 `MessageModel.content` 非空约束。

#### Scenario: 命中 skill 并注入

- **WHEN** 用户消息为 `/finance-qa 腾讯营收`
- **THEN** 注入 `finance-qa` 的方法论（隐藏消息），本轮以该方法论执行

#### Scenario: 注入内容进入历史

- **WHEN** 注入发生在第 N 轮
- **THEN** 第 N+1 轮仍可从会话上下文读到该 skill 内容，直到压缩或新会话

#### Scenario: inline 单轮执行

- **WHEN** `/xxx` 命中的是 `context: inline` skill
- **THEN** 主 agent 单轮携带该方法论作答（正常一轮 LLM）

#### Scenario: fork 直出（主 agent 零 LLM 轮）

- **WHEN** `/xxx` 命中的是 `context: fork` skill
- **THEN** 直接调用 fork 子代理产出结果，主 agent 不先产生 LLM 轮
- **AND** 子代理结果交由 `verify` 节点校验

#### Scenario: fork 校验不通过重跑

- **WHEN** 子代理结果未通过 `verify`
- **THEN** 重跑子代理（总次数不超过 1 次）

#### Scenario: 未知 skill 提示（不静默）

- **WHEN** 用户消息为 `/不存在的skill 问题`
- **THEN** 返回"skill 不存在 + 可用列表"，不当作普通文本

#### Scenario: 非命令前缀按普通文本

- **WHEN** 消息不以 `/` 开头，或 `/` 后不构成命令形态（如 `/ 今天天气`）
- **THEN** 按普通文本处理，不注入、不报错

#### Scenario: 落库保留原文

- **WHEN** 用户发送 `/finance-qa 腾讯2024营收`
- **THEN** 会话历史中该条 user 消息内容为原文 `/finance-qa 腾讯2024营收`（回放显示与用户输入一致）

#### Scenario: 组装 prompt 时剥离前缀

- **WHEN** 构建本轮 prompt（当前轮 query 与历史 user 消息）
- **THEN** user 消息内容为剥离前缀后的 `腾讯2024营收`，模型看不到 `/finance-qa`
- **AND** 该 skill 的方法论仍以隐藏消息形式存在，持续生效

#### Scenario: 仅命令无剩余文本

- **WHEN** 用户只发送 `/finance-qa`（无剩余文本）
- **THEN** 剥离后不产生空的 user 消息（该轮仅凭注入的隐藏消息与用户后续输入处理），不违反消息内容非空约束
