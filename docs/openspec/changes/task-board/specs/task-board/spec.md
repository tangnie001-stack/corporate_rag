# task-board Specification (Delta)

## ADDED Requirements

### Requirement: Task 工具集

系统 SHALL 提供 Task 工具（create/get/list/update/output/stop），仅主 agent 可见；读写**会话级**进程内任务注册表（key 含 session_id）。主 agent 经 Task 工具创建/更新的条目为 `type=plan`（计划/跟踪项）。

#### Scenario: 创建计划任务
- **WHEN** 主 agent 需要跟踪一个计划项
- **THEN** 创建 `type=plan` 条目并返回 task_id

#### Scenario: 更新状态与依赖
- **WHEN** 计划项进展或完成
- **THEN** 可标记状态（进行中/完成/失败）并可选声明依赖

#### Scenario: 列表与查询
- **WHEN** 主 agent 或前端需要了解任务
- **THEN** 经 Task 工具或 SSE 读取任务快照

### Requirement: 委派执行自动登记

delegate fork 启动 SHALL 自动登记 `type=execution` 条目，且 **task_id=delegate_id**（同一 id 贯通），结束更新终态（done/failed/timeout+原因）；execution 与 plan 条目不互相覆盖。

#### Scenario: 委派期间看板可见
- **WHEN** fork 子代理运行中
- **THEN** 看板显示 execution 条目及其实时活动摘要

#### Scenario: 委派结束更新
- **WHEN** fork 完成/超时/失败
- **THEN** execution 条目更新为终态（含超时原因）

### Requirement: 任务快照读取

系统 SHALL 提供按会话读取任务快照的接口（如 `GET /api/sessions/tasks?session_id=`，权限同 sessions/events），供前端在页面刷新/切换会话后初始化看板，而非仅依赖运行期 SSE 增量。

#### Scenario: 刷新后恢复看板
- **WHEN** 页面刷新或切换会话
- **THEN** 前端经快照接口取回该会话既有任务并渲染看板

#### Scenario: 无任务返回空
- **WHEN** 会话无任何任务
- **THEN** 快照接口返回空列表

### Requirement: 注册表生命周期

注册表条目 SHALL 带 TTL（默认 30min）惰性清理；同一会话新 POST SHALL 保留既有任务直至 TTL，仅清空本轮事件缓冲。

#### Scenario: 跨轮与新 POST 保留
- **WHEN** 同会话发起新一轮生成
- **THEN** 既有任务保留（直至 TTL），本轮事件缓冲清空

### Requirement: task 事件与看板 UI

注册表变更 SHALL 推送 SSE `task` 事件（action=created|updated|terminal，payload=含 type/delegate_id/status 的任务快照）；前端提供只读可折叠"任务/进度"看板。

#### Scenario: 事件推送
- **WHEN** 注册表条目创建或更新
- **THEN** 推送 task 事件，前端看板刷新

#### Scenario: 前端只读
- **WHEN** 前端展示看板
- **THEN** 仅渲染，不提供写入口

#### Scenario: 与过程区关联
- **WHEN** 看板条目为 execution 类型
- **THEN** 条目携带 delegate_id，可与"分析过程"折叠区事件关联
