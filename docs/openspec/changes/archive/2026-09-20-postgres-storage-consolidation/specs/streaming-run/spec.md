## MODIFIED Requirements

### Requirement: 任务状态查询

系统 SHALL 提供 `GET /api/sessions/task-status`：缓冲存在且无终态 → `generating`（含可续接标记与当前缓冲 seq）；缓冲有终态或关系型库存在 assistant 消息 → `completed`；无缓冲且无 assistant → `idle`（不得返回 generating）。

#### Scenario: 生成中可续接
- **WHEN** 缓冲存在且无 done/error 终态
- **THEN** 返回 `status=generating` 与当前缓冲 seq

#### Scenario: 进程死亡兜底
- **WHEN** 无缓冲且无 assistant 消息
- **THEN** 返回 `status=idle`，前端不得持续轮询或显示生成中
