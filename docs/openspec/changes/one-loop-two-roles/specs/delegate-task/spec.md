# delegate-task Specification (Delta)

> ⚠️ **协调提示（归档前必读）**：本 delta 的 MODIFIED 块**基于当前主规格文本**。`skill-execution-and-delegation` 的 delta **同样 MODIFIED 这条 `fork 执行` requirement**（它改子代理工具面：默认继承 + 只读收窄 + 禁用集）。两者归档顺序为「它先、本变更后」⇒ **本块在同步前 MUST 重新复制其落地后的 requirement 全文再施加本节改动**，否则会把它的工具面改动回退掉。

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
- **AND** 子代理 SHALL 经由与主 agent **共用的装配工厂**生成（见 `agent-assembly` 的「主/子角色共用同一 agent 装配」）；其 `middleware` SHALL 按角色装配——子角色按自身需要（如观测）装配，主角色另装配 system 施加 / 回合预算 / 观测三件套。原先「middleware 参数保留装配位但默认传空（v1 不启用）」的表述自本变更起不再成立

#### Scenario: fork 执行者的选择顺序

- **WHEN** 一个 fork skill 被执行
- **THEN** 执行者按优先级：skill 的 `agent:` 声明 > 本会话选定智能体 > 系统默认 prompt

#### Scenario: 模型覆盖

- **WHEN** skill frontmatter 声明了 model
- **THEN** fork 子代理用 `get_llm(model=record.model)` 新建实例（仅 fork 生效）
- **WHEN** skill 未声明 model
- **THEN** 复用主 agent 的 llm 实例
