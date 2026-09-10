## ADDED Requirements

### Requirement: system prompt 三层组装

系统构建 system prompt SHALL 按三层组装：① **人设层**（可替换）、② **环境约束层**（系统强制注入）、③ **运行时层**（消息级，不属于 system prompt）。

#### Scenario: 三层边界

- **WHEN** 构建本轮 system prompt
- **THEN** 人设层来自"选定的智能体预设正文"或"未选智能体时的系统默认 prompt"
- **AND** 环境约束层由系统按会话条件（KB 绑定 / 有无可用 skill）注入
- **AND** 运行时内容（inline skill 正文、verify 指引、时间上下文）以消息形式追加，不写入 system prompt

### Requirement: 人设层可替换

未选定智能体时，人设层 SHALL 为 `PromptManager.get_system_prompt()`（保持既有行为）；选定智能体后，人设层 SHALL 替换为该 AgentPreset 的正文。

#### Scenario: 未选智能体沿用默认

- **WHEN** 会话未选定智能体
- **THEN** system prompt 的人设层与既有实现**逐字一致**（不可因重构产生行为漂移）

#### Scenario: 选定智能体替换人设

- **WHEN** 会话选定"财务专家"（AgentPreset 正文为"你是一名资深财务分析师…"）
- **THEN** system prompt 的人设层为该正文

### Requirement: 环境约束层系统强制叠加

环境约束层 SHALL 由系统注入且**不可被智能体预设覆盖**：会话绑定 KB 时 SHALL 注入检索纪律（先检索后作答）与引用标注要求（`[n]`）；未绑定 KB 时 SHALL 注入"禁止调用检索工具"的会话指令。

#### Scenario: 绑定 KB 时保留引用要求

- **WHEN** 会话绑定 KB 且选定任意智能体
- **THEN** system prompt 同时包含该智能体人设与检索纪律、引用标注要求
- **AND** 回答仍带 `[n]` 引用（引用链不因换人设而失效）

#### Scenario: 预设不能覆盖环境约束

- **WHEN** 某智能体预设正文未提及引用要求
- **THEN** 系统仍强制注入引用标注要求（约束不依赖预设作者）

#### Scenario: 未绑定 KB 禁止检索

- **WHEN** 会话未绑定 KB
- **THEN** 注入禁止检索的会话指令（既有 `KB_UNBOUND_SYSTEM_PROMPT` 语义），无论是否选定智能体

### Requirement: 环境约束内容不依赖远端 prompt 管理

环境约束层内容 SHALL 来自本地常量，不经远端 prompt 管理（如 Langfuse）拉取，以保证系统规则不因远端改动而消失。

#### Scenario: 引用要求属于环境约束层

- **WHEN** 组装 system prompt
- **THEN** 引用标注要求（原 `INLINE_CITATION_INSTRUCTION`）位于环境约束层、取自本地常量
