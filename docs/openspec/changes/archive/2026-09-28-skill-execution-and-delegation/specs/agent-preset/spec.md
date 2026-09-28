# agent-preset Specification (Delta)

## MODIFIED Requirements

### Requirement: 智能体作为会话主 agent 人设

选定的智能体 SHALL 作为本会话主 agent 的身份：其正文注入主 agent 的 system prompt 人设层（组装规则见 `prompt-composition`）；`skills` 中列出的 skill SHALL 在该会话首轮生成前注入一次（隐藏消息，与 `/xxx` 同路径，不进 system prompt）。主 agent 的编排骨架（循环 / 工具 / verify / format）不变，**其工具集也不因预设声明 `tools` 而改变**（`tools` 只作用于该预设作为 fork 执行者时，见 `delegate-task`）。**v1 不含 `model`**，预设不能覆盖主模型。

#### Scenario: 人设注入

- **WHEN** 会话选定智能体"财务专家"
- **THEN** 主 agent 以该预设正文作为 system prompt 人设层回复，直至会话结束

#### Scenario: 预加载 skill

- **WHEN** 预设声明 `skills: [a]`（`a` 为 inline skill）且为该会话首轮
- **THEN** 该 skill 的 **inline 正文**在首轮生成前以隐藏消息注入会话上下文（不进 system prompt）
- **AND** 后续轮次持续可见且不重复注入

#### Scenario: fork skill 不被预加载

- **WHEN** 预设声明的 `skills` 列表中含 `context: fork` 的 skill
- **THEN** 记 warning 并跳过该项（**不注入其 `fork_body`**），其余项正常预加载
- **AND** 理由：`fork_body` 是写给子代理的任务/方法论 prompt，注入主 agent 会造成语义错配，且与 fork 的"正文不进主 agent 上下文"直接冲突
- **AND** 需要使用 fork skill 时由主 agent 通过委派机制触发，不靠预加载

#### Scenario: 预加载声明的 skill 不存在

- **WHEN** 预设声明了 `skills: [ghost-skill]` 但注册表中无该 skill
- **THEN** 记 warning 并跳过该项（不中断会话、不影响其余预加载项）
- **AND** 预设本身仍正常加载（`skills` 是内容引用，不是加载期硬依赖）

#### Scenario: 预设 tools 不改主 agent 工具集

- **WHEN** 会话选定预设并声明了 `tools: [retrieve_kb]`
- **THEN** 主 agent 可用工具集与未选预设时一致（`tools` 仅在 fork 子代理路径生效）

#### Scenario: 默认智能体

- **WHEN** 会话未选定任何智能体
- **THEN** 主 agent 使用系统默认 system prompt（既有行为不变）
