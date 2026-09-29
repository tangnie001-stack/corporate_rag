# prompt-composition Specification

## Purpose
TBD - created by syncing change session-agent-and-skill-invocation. Update Purpose after archive.

## Requirements


### Requirement: system prompt 三层组装

系统构建 system prompt SHALL 按三层组装：① **人设层**（可替换）、② **环境约束层**（系统强制注入）、③ **运行时层**（消息级，**不属于** system prompt）。组装 SHALL 由统一的 `build_system_prompt(persona, kb_bound, has_skills)` 完成，`src/rag/prompt.py` 的两处调用点（`build_prompt`、`build_simple_prompt`）SHALL 都走它。

#### Scenario: 三层边界

- **WHEN** 构建本轮 system prompt
- **THEN** 人设层来自"选定的智能体预设正文"或"未选智能体时的系统默认人设"
- **AND** 环境约束层由系统按会话条件（KB 绑定 / 有无可用 skill）注入，并包含当日日期锚点
- **AND** 运行时内容（inline skill 正文、预设预绑定 skill、verify 指引）以消息形式追加，**不写入 system prompt**

### Requirement: 人设层取 base prompt（不含系统追加段）

人设层 SHALL 取自 `PromptManager.get_base_system_prompt()`（Langfuse 拉取或本地兜底**原文**，**不含**引用指令 / 委派引导 / 日期等系统追加段）；选定智能体后 SHALL 替换为该 AgentPreset 的正文。

**理由**：现状 `get_system_prompt()` 在返回前幂等追加引用指令、委派引导与日期；若人设层直接复用它，这些段会既留在人设层、又由环境约束层再注入一次（重复注入），且违反"引用要求归环境约束层"。

#### Scenario: 未选智能体沿用默认人设

- **WHEN** 会话未选定智能体
- **THEN** 人设层为 `get_base_system_prompt()` 的原文

#### Scenario: 选定智能体替换人设

- **WHEN** 会话选定"财务专家"（AgentPreset 正文为"你是一名资深财务分析师…"）
- **THEN** system prompt 的人设层为该正文

#### Scenario: 不重复注入系统追加段

- **WHEN** 组装 system prompt
- **THEN** 引用指令 / 委派引导 / 日期各出现一次（来自环境约束层），不因"人设层已含"而重复

### Requirement: 环境约束层系统强制叠加

环境约束层 SHALL 由系统注入且**不可被智能体预设覆盖**，内容**取自本地常量、不经远端 prompt 管理**：

- 引用标注要求（`INLINE_CITATION_INSTRUCTION`）——始终注入
- 委派引导（`DELEGATE_GUIDANCE_SECTION`）——存在可用 skill 时注入
- 会话绑定 KB 时：检索纪律（先检索后作答）
- 未绑定 KB 时：`KB_UNBOUND_SYSTEM_PROMPT`（禁止调用检索工具）
- 当日日期锚点

追加**顺序 SHALL 与重构前一致**（base → 引用指令 → 委派引导 → 日期），以保证行为不漂移。

#### Scenario: 绑定 KB 时保留引用要求

- **WHEN** 会话绑定 KB 且选定任意智能体
- **THEN** system prompt 同时包含该智能体人设与检索纪律、引用标注要求
- **AND** 回答仍带 `[n]` 引用（引用链不因换人设而失效）

#### Scenario: 预设不能覆盖环境约束

- **WHEN** 某智能体预设正文未提及引用要求
- **THEN** 系统仍强制注入引用标注要求（约束不依赖预设作者）

#### Scenario: 有可用 skill 时注入委派引导

- **WHEN** 会话存在可用 skill
- **THEN** system prompt 注入 `DELEGATE_GUIDANCE_SECTION`（委派能力对模型可见）

#### Scenario: 未绑定 KB 禁止检索

- **WHEN** 会话未绑定 KB
- **THEN** 注入禁止检索的会话指令（既有 `KB_UNBOUND_SYSTEM_PROMPT` 语义），无论是否选定智能体

#### Scenario: 环境约束不依赖远端

- **WHEN** 组装 system prompt
- **THEN** 引用标注要求（原 `INLINE_CITATION_INSTRUCTION`）与委派引导取自本地常量，不因远端 prompt（Langfuse）改动而消失

### Requirement: 默认行为逐字不变（端到端快照）

未选定智能体时，组装结果 SHALL 与重构前的 system prompt **端到端逐字一致**（含引用指令、委派引导、日期三段的顺序与内容）。

#### Scenario: 未选智能体快照对比

- **WHEN** 会话未选定智能体，用同一份 base prompt 组装
- **THEN** `build_system_prompt(persona=None, kb_bound=True, has_skills=…)` 的 system 段与重构前 `build_prompt` 的 system 段逐字一致

> 注：一致性以**端到端输出**为准，而非"人设层逐字一致"——后者与"把系统追加段移出人设层"自相矛盾。

### Requirement: system 段的施加通道逐字不变

system 段的**组装**结果送入模型请求时 SHALL 逐字不变——包括未绑定 KB 时由 `build_system_prompt` 产出的**第二条** system 消息（未绑定提示）SHALL 仍然作为独立 system 消息下发，SHALL NOT 被合并进第一条、也 SHALL NOT 降级为普通消息。

组装仍由 `build_system_prompt(...)` 承担、**每次生成 SHALL 只执行一次**；本要求只约束**施加通道**（从组装结果到模型请求这一段），不改变三段式边界与追加顺序。

组装结果 SHALL 由**跨轮次持久**的载体承载，使**重生成轮**（校验节点触发、重新进入主循环）仍能取到同一份 system 段、SHALL NOT 在重生成轮因"消息非空而跳过组装"导致 system 段丢失；同时重生成轮 SHALL NOT 触发第二次组装。

#### Scenario: 未绑 KB 时两条 system 消息都到位

- **WHEN** 会话未绑定 KB，本轮组装产出两条 system 消息
- **THEN** 模型请求中 SHALL 依次包含这两条 system 消息，内容与组装结果逐字一致

#### Scenario: 每次生成只组装一次

- **WHEN** 一次生成内主循环发生多轮模型调用
- **THEN** system 段组装 SHALL 只发生一次，其组装事实日志（`prompt assembled` 语义的事件）SHALL 每次生成只产出一次

#### Scenario: 消息构成计数逐字不变

- **WHEN** 一次生成产出消息构成日志（`prompt messages` 语义的事件）
- **THEN** 其 `system_msgs` / `injected_msgs` / `history_msgs` 三个计数 SHALL 与变更前逐字一致
- **AND** `system_msgs` SHALL NOT 因 system 段被从消息列表中拆出（改由施加通道单独下发）而变为 0

#### Scenario: 重生成轮仍带完整 system 段

- **WHEN** 校验节点要求重生成，主循环被再次进入
- **THEN** 重生成轮的模型请求 SHALL 仍包含完整的 system 段（含未绑 KB 时的第二条）
- **AND** SHALL NOT 触发第二次 system 段组装

#### Scenario: 组装通道变化不改变快照

- **WHEN** 施加通道改由 middleware 承担
- **THEN** 「默认行为逐字不变（端到端快照）」要求 SHALL 仍然成立（以端到端输出为准）
