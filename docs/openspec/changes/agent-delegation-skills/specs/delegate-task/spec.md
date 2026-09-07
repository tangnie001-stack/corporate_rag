# delegate-task Specification (Delta)

## ADDED Requirements

### Requirement: delegate_task 工具
系统 SHALL 在主 agent 工具集新增 `delegate_task(task, skill)` 工具。

#### Scenario: 调用入口

- **WHEN** 主 agent 判断任务需领域能力
- **THEN** 调 delegate_task 并传入 task（任务描述）与 skill（要用的 skill 名）
- **AND** 工具的 description 动态列出可用 skill（名 + whenToUse，预算截断）

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
- **THEN** 用 create_react_agent 生成独立子代理
- **AND** 子代理 system_prompt = skill 的 agent_prompt
- **AND** 子代理初始消息 = task
- **AND** 子代理工具集 = 空（allowed-tools 字段预留，本版恒不启用；现有业务工具 retrieve_kb/search_web 均写主请求共享 ctx，子代理持工具会污染主 agent tool_contexts——见 design D7）

#### Scenario: 材料由主 agent 预检索

- **WHEN** fork 子代理需要检索材料
- **THEN** 主 agent 在 delegate 前先检索好，把 context 作为 task 一部分传入
- **AND** fork 子代理工具集恒空（不含 retrieve_kb/search_web/delegate_task），材料全凭 task 携带
- **AND** fork skill 正文不出现任何工具名（零工具下子代理无工具可调，正文声明工具集属误导）

#### Scenario: 模型覆盖

- **WHEN** skill frontmatter 声明了 model
- **THEN** fork 子代理用 `get_llm(model=record.model)` 新建实例
- **WHEN** skill 未声明 model
- **THEN** 复用主 agent 的 llm 实例

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
系统 SHALL 禁止子代理再次调用 delegate_task。

#### Scenario: 零工具硬保证

- **WHEN** 构造 fork 子代理工具集
- **THEN** 工具集为空（零工具设计，见 design D7）
- **AND** 因工具集恒空，delegate_task 永不进入子代理可调用范围（防递归由零工具硬保证）
