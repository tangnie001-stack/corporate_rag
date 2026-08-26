# batch-clarification Specification

## Purpose
TBD - created by archiving change query-rewrite-and-graph-simplification. Update Purpose after archive.
## Requirements
### Requirement: 一次往返批量澄清

系统 SHALL 在 classify 检测到缺失实体时，一次性列出**所有**缺失维度（按信息增益排序），通过单个 clarification 事件携带 `questions` 列表（每个元素含 type/question/suggestions）发送给前端；前端渲染多问题表单，用户一次选择/填写后**组合成一条消息**提交，落会话历史一条。不得为每个缺失维度逐轮追问。

#### Scenario: 多缺失维度一次问完
- **WHEN** classify 判定 query 同时缺失公司/指标/期间三个维度
- **THEN** 单次 clarification 事件携带三个 question 及各自 suggestions，前端一次展示，用户一次提交

#### Scenario: 组合消息落入历史
- **WHEN** 用户对多问题表单一次提交（如"东软集团 毛利率 2025年第一季度"）
- **THEN** 组合文本作为一条 user 消息写入会话历史，下一轮 classify 从中提取全部实体并直接执行，不再追问

#### Scenario: 维度数封顶
- **WHEN** 缺失维度超过 4 个
- **THEN** 表单最多展示 4 个维度，其余走通用输入框"请补充完整信息"

### Requirement: 澄清选项防兜底乱选

系统 SHALL 为每个澄清维度的"其他"选项提供自由输入框，避免用户选择空兜底导致实体缺失无法恢复。用户一次回答后若仍缺次要维度，系统 SHALL best-guess 执行（可逆低风险查询），不再连环追问。

#### Scenario: 其他选项可输入
- **WHEN** 用户在澄清表单选择"其他"
- **THEN** 对应维度出现输入框，用户输入的自定义值被纳入组合消息

#### Scenario: 部分缺失 best-guess 执行
- **WHEN** 用户一次回答后仍有次要维度缺失（如 KB 仅一家公司可推断）
- **THEN** 系统基于已有实体与 KB 候选推断执行，不发起第二轮澄清

### Requirement: abstention 引导

系统 SHALL 在检索空导致 abstention 时，发送一次澄清引导事件，基于 KB 候选实体提示可查询的指标/维度（如"东软 2025Q1 未披露毛利率，可查询：营收/净利润…"）；无候选时不发送引导，保持纯 abstention 文案。引导只发送一次，不连环。

#### Scenario: abstention 时提示可查指标
- **WHEN** 检索空返回 abstention 且 KB 有候选实体
- **THEN** 发送一次引导澄清事件，question 说明未找到的数据，suggestions 为可查询的候选指标

#### Scenario: 无候选保持纯文案
- **WHEN** 检索空且 KB 无候选实体
- **THEN** 仅返回"未在文档中找到相关数据"文案，不发送引导事件
