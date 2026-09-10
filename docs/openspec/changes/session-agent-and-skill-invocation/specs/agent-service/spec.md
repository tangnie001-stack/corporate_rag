## ADDED Requirements

### Requirement: 会话智能体人设注入

AgentService SHALL 接收流式请求的 `agent` 字段（会话选定智能体名，可空）；非空时按名解析 AgentPreset，将其正文作为本会话主 agent 的 system prompt（人设层），`skills` 预加载进上下文。`agent` 为空或解析失败时 SHALL 使用系统默认 system prompt（既有行为不变）。

#### Scenario: 请求携带 agent

- **WHEN** 流式请求 body 含 `agent="finance-expert"`
- **THEN** 本轮生成以该预设正文作为主 agent 的 system prompt

#### Scenario: 未知 agent 降级

- **WHEN** 请求的 agent 名在注册表中不存在
- **THEN** 降级为默认 system prompt，并记 warning（不报错、不中断）

#### Scenario: agent 为空

- **WHEN** 请求未携带 agent
- **THEN** 使用系统默认 system prompt，行为与既有一致

### Requirement: `/xxx` 前缀在服务层的路由

AgentService SHALL 在生成入口解析用户消息的 `/name` 前缀：命中 skill 则将其内容注入会话上下文（隐藏消息）并按其 context 执行，跳过模型委派判断；未命中则按普通文本处理。inject 后的 skill 内容 SHALL 保留在会话上下文中供后续轮次使用。

#### Scenario: 命中 skill 并注入
- **WHEN** 用户消息为 `/finance-qa 腾讯营收`
- **THEN** AgentService 注入 finance-qa 的方法论（隐藏消息），本轮以该方法论执行

#### Scenario: 注入内容进入历史
- **WHEN** 注入发生在第 N 轮
- **THEN** 第 N+1 轮仍可从会话上下文读到该 skill 内容，直到压缩或新会话

#### Scenario: 未命中按普通文本
- **WHEN** 消息以 `/` 开头但名字不是已注册 skill
- **THEN** 按普通文本处理，不注入、不报错

### Requirement: 会话智能体的持久化与不可变校验

AgentService SHALL 将会话选定的智能体名持久化到会话状态；请求携带的 `agent` 与会话已记录值不一致时 SHALL **拒绝**（不可中途更换，需新建会话）。

#### Scenario: 会话已绑定智能体

- **WHEN** 会话已记录 `agent=finance-expert`，新请求携带 `agent=tax-expert`
- **THEN** 拒绝该请求（不可变），不执行生成

#### Scenario: 首轮无 agent

- **WHEN** 新会话首轮未携带 `agent`
- **THEN** 使用系统默认 prompt，会话不记录智能体（或记录为空）

#### Scenario: 同一 agent 重复携带

- **WHEN** 请求携带的 `agent` 与会话已记录值一致
- **THEN** 正常执行（每轮携带 + 后端校验一致）
