# ask-user-mode Specification

## Purpose
TBD - created by archiving change web-search-fallback. Update Purpose after archive.

## Requirements

### Requirement: ask_user 模型自带选项

`AskQuestion` SHALL 支持 `options` 字段，允许模型自行提供澄清候选选项，对齐 deepseek-harness `ask_user_question` 的自由格式语义。

#### Scenario: 非 KB 问题模型自带选项
- **WHEN** 非知识库问题需要澄清
- **THEN** 模型 SHALL 可自行构造 question 与 options，系统不注入 KB 候选

### Requirement: ASK_USER_MODE_DSH 开关

系统 SHALL 提供 `ASK_USER_MODE_DSH` 开关（默认开启）控制 ask_user 澄清模式。

#### Scenario: 开启为 dsh 全自由格式
- **WHEN** `ASK_USER_MODE_DSH=true`
- **THEN** 所有 ask_user 问题的选项由模型提供（可为空 = 纯文本问题），系统不按 dimension 注入候选

#### Scenario: 关闭为双模式
- **WHEN** `ASK_USER_MODE_DSH=false`
- **THEN** 知识库相关问题按 dimension 由系统注入 KB 真实候选（防编造）；非知识库问题使用模型自带 options
