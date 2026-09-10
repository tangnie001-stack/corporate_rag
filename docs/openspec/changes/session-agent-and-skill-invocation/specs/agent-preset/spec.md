## ADDED Requirements

### Requirement: 智能体预设文件结构

系统 SHALL 从项目根 `agents/<name>.md` 加载智能体预设。文件 SHALL 为 Markdown：YAML frontmatter 声明元数据，正文为智能体的 system prompt（人设）。

frontmatter 字段：`name`（缺省用文件名）、`description`（何时使用；缺省用正文首段）、`tools`（可选，允许的工具名列表；缺省继承全部）、`skills`（可选，预加载的 skill 名列表）、`maxTurns`（可选，子代理最大执行轮次）。**v1 不含 `model`**（本项目模型不可选，多模型留待单独设计）。命名风格用**驼峰**（`maxTurns`）。

#### Scenario: 文件识别与解析

- **WHEN** 扫描 `agents/` 目录
- **THEN** 每个 `.md` 文件识别为一个智能体预设
- **AND** 正文作为 system prompt，frontmatter 解析出 name/description/model/tools/skills

#### Scenario: 工具缺省继承

- **WHEN** 预设未声明 `tools`
- **THEN** 该智能体继承会话可用工具全集

#### Scenario: 目录缺失

- **WHEN** `agents/` 目录不存在或为空
- **THEN** 智能体选择器仅提供默认智能体（通用助手），不报错

### Requirement: AgentPreset 运行时对象

系统 SHALL 为每个加载的智能体预设生成 AgentPreset 对象，含 {name, description, system_prompt, tools, skills, max_turns, source_path}（v1 不含 model）。

#### Scenario: 注册表按名索引

- **WHEN** 加载完成后按名查询
- **THEN** 返回对应 AgentPreset；不存在返回 None（调用方降级到默认智能体）

#### Scenario: 同名冲突

- **WHEN** 两个预设声明同名
- **THEN** 加载期 fail-fast 报错（禁止静默覆盖）

### Requirement: 智能体的会话级选择与不可变

新建对话页 SHALL 提供智能体选择器；选定的智能体 SHALL 仅作用于本次会话，且**会话内不可更改**；更换智能体 SHALL 新建对话重新选择。未选择时 SHALL 使用默认智能体（通用助手）。

#### Scenario: 会话内不可更改

- **WHEN** 用户在已开始的会话中尝试更换智能体
- **THEN** 系统不允许更改，提示需新建对话

#### Scenario: 未选择使用默认

- **WHEN** 用户未选择智能体直接提问
- **THEN** 系统使用默认智能体（通用助手），行为与既有一致

#### Scenario: 会话记录回显

- **WHEN** 打开一个选定了智能体的历史会话
- **THEN** 顶栏或会话信息中回显该智能体名

### Requirement: 智能体作为会话主 agent 人设

选定的智能体 SHALL 作为本会话主 agent 的身份：其正文注入主 agent 的 system prompt，`model` 覆盖主模型（如声明），`skills` 中列出的 skill 在会话开始时预加载进上下文。主 agent 的编排骨架（循环 / 工具 / verify / format）不变。

#### Scenario: 人设注入

- **WHEN** 会话选定智能体"财务专家"
- **THEN** 主 agent 以该预设正文作为 system prompt 回复，直至会话结束

#### Scenario: 预加载 skill

- **WHEN** 预设声明 `skills: [财报分析手册]`
- **THEN** 该 skill 内容在会话开始时加载进上下文，用户无需用 `/xxx` 触发

#### Scenario: 默认智能体

- **WHEN** 会话未选定任何智能体
- **THEN** 主 agent 使用系统默认 system prompt（既有行为不变）

### Requirement: 智能体预设作为 fork 执行者来源

`context: fork` 的 skill SHALL 默认由本会话选定的智能体预设执行；会话未选定智能体且 skill 未声明 `agent:` 时 SHALL 使用默认执行者（`general-purpose`）。

**执行者优先级**：skill 的 `agent:` 声明 > 本会话选定智能体 > `general-purpose` 默认。

**fork 的 prompt 结构** SHALL 为：system prompt = 执行者人设（agent preset 正文 / 默认执行者的系统默认 prompt），user message = skill 内容（任务/方法论），tools = 执行者 tools ∩ skill allowed-tools。**人设来自 agent，任务来自 skill，二者不混。**

**`general-purpose` 默认在本项目 SHALL 指系统默认 system prompt**（`PromptManager.get_system_prompt()`，即未选智能体时主 agent 用的 prompt），不得新造 prompt。

#### Scenario: 会话智能体作为执行者

- **WHEN** 会话选定"财务专家"且用户 `/xxx` 调用一个 `context: fork` 的 skill
- **THEN** fork 子代理以"财务专家"人设（system prompt）执行该 skill
- **AND** skill 内容作为 user message 传入子代理

#### Scenario: 无会话智能体时用默认执行者

- **WHEN** 会话未选定智能体，用户 `/xxx` 调用一个 `context: fork` 且未声明 `agent:` 的 skill
- **THEN** fork 子代理以系统默认 system prompt 作为人设执行，skill 内容作为 user message

#### Scenario: skill 覆盖执行者

- **WHEN** skill 显式声明 `agent: <名>`
- **THEN** 该 skill 的 fork 使用指定预设执行，覆盖会话选定值
