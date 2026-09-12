# chat-harness-ui Delta（source-tier-labeling）

## ADDED Requirements

### Requirement: 引用来源权威等级徽标

仅引用抽屉条目 SHALL 展示来源权威等级徽标（官方一手/权威媒体/一般/UGC/内部文档，对应 citation 的 tier 字段，位于来源名称之前）；引用横条保持既有形态不渲染徽标（设计已确认）。实时路径与历史回放路径行为一致；tier 缺失（存量消息）SHALL 不显示徽标，其余引用功能不受影响。

#### Scenario: 实时路径展示等级

- **WHEN** 实时流 citation 帧携带 tier 字段
- **THEN** 打开引用抽屉后，条目 SHALL 在来源名称之前展示对应等级标识

#### Scenario: 历史回放展示等级

- **WHEN** loadSessionMessages 重建引用（sources 含 tier）
- **THEN** 重建的抽屉条目 SHALL 与实时路径展示相同的等级徽标

#### Scenario: 存量数据无 tier

- **WHEN** citation 数据缺少 tier 字段（存量消息）
- **THEN** 不显示徽标，横条/抽屉/snippet 功能正常，无 JS 错误
