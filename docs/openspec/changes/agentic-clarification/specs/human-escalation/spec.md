## ADDED Requirements

### Requirement: 阶段一 abstention 转人工入口

系统 SHALL 在 agent 循环结束时由后端判定 abstention（`state.tool_contexts` 为空 **或** 最终答案匹配 `ABSTENTION_MARKERS`），发 `SSEAbstentionEvent`（携带"可转人工"标记）；前端收到后展示转人工入口（按钮/联系方式）。此阶段不创建工单，仅 UI 层提示 + 会话记录标记。

#### Scenario: abstention 展示转人工入口
- **WHEN** 后端判定 abstention 并发送 SSEAbstentionEvent
- **THEN** 前端在 abstention 消息旁展示"转人工咨询"入口，点击记录会话标记

#### Scenario: 正常回答不发事件
- **WHEN** agent 循环正常生成带引用的回答
- **THEN** 不发送 SSEAbstentionEvent，前端不显示转人工入口

### Requirement: 阶段二 escalate_to_human 工具

系统 SHALL 在 agent 循环中提供 `escalate_to_human` 工具（fire-and-forget）：模型在检索后证据仍不足/证据矛盾/问题超出知识库范围时调用；工具创建工单记录（reason、context_summary、attempted_actions、session_id）并返回工单号；创建后立即返回，不阻塞等待人工处理；模型随后告知用户"已转人工，工单号 #xxx"。工单的状态流转/分配/通知/人工控制台属产品层，不在工具职责内。

#### Scenario: 证据不足转人工
- **WHEN** 模型检索后判断证据仍不足且问题需要权威答复
- **THEN** 调用 escalate_to_human 创建工单，返回工单号，agent 告知用户已转人工

#### Scenario: 工单工作流独立
- **WHEN** 工单已创建
- **THEN** 工单状态/分配/通知由后端产品层管理，与 agent 循环解耦

### Requirement: 转人工护栏

系统 SHALL 限制单 turn 内 escalate_to_human 最多调用一次（防止模型无脑甩锅）；system prompt 要求"先尝试检索并判断证据不足才能转人工"。

#### Scenario: 超限拒绝
- **WHEN** 模型在单 turn 内第二次调用 escalate_to_human
- **THEN** 工具返回错误，模型基于现有信息收尾
