# task-board Design

## Context

委派（fork）与后台执行目前没有全局可见的任务/进度视图；前端仅在回答内可见（由 core change 提供过程折叠区）。参照 claude-code 的 Task*Tool（create/get/list/update/output/stop）与 TaskListV2 面板，提供主 agent 侧的任务跟踪能力与前端只读看板。数据源为会话级进程内注册表，不引入独立异步任务框架（Non-goal）。

## Goals / Non-Goals

**Goals:**
- 主 agent 可建任务、更新状态/依赖，用户可看全局任务与"子代理正在做什么"
- 委派执行自动登记/终态，与主 agent 计划项共用注册表且可区分、可关联
- 看板与回答内过程折叠区互补：看板=全局状态，过程区=本次原文

**Non-Goals:**
- 不引入独立异步任务/后台 agent 框架；不持久化到 DB（进程内 + TTL）
- 不做前端写（前端只读）；Task 工具仅主 agent 可见

## Decisions

### D1 注册表：会话级进程内 + TTL
key 含 `session_id`；条目 `task_id/title/type/status/stage/summary/delegate_id/dependencies/created_at/updated_at`；execution 条目 `task_id=delegate_id`（同一 id 贯通，供看板/过程区关联）；TTL 30min 惰性清理（仿事件缓冲 sweep）；同一会话新 POST 保留既有任务直至 TTL，仅清本轮事件缓冲。

### D2 双写入口语义区分（type）
- 主 agent 经 Task 工具创建 → `type=plan`（计划/跟踪项）；
- delegate fork 自动登记 → `type=execution`，`task_id=delegate_id`；
- UI 按 type 展示/分组，execution 条目与过程区（delegate_id）关联；plan 与 execution 不互相覆盖。

### D2b 任务快照读取
新增 `GET /api/sessions/tasks?session_id=`（权限同 sessions/events）：返回该会话注册表任务快照；前端页面刷新/切换会话后调用以初始化看板，补齐仅靠 SSE 增量的缺口。

### D3 Task 工具与写权限
create/get/list/update/output/stop 最小集，经 ctx.session_id 定位；仅主 agent 工具面暴露，前端只读（前端经 SSE task 事件渲染）。
**stop 语义（已收敛）**：fork 在 ToolNode 内阻塞式串行执行——主 agent 无法在 delegate 运行期间并行调 task_stop；跨请求 delegate 属旧请求 ContextVar，本请求 abort 触达不到；同请求竞态 set abort 会误杀整个主请求（非仅该 delegate）。因此 task_stop **不**置位 `ctx.abort_signal`，仅对非终态条目（含残留 running/pending）置 cancelled（终态 action=terminal，reason 取 `DelegateStopReason.CANCELLED`）；运行中 delegate 的真实取消收敛到 cancel 端点（core：fork 响应同一 `ctx.abort_signal` → reason=cancelled → delegate end 与注册表终态一致）。中断/终态 reason 统一取 core 的 `DelegateStopReason`（normal/idle/total/turn/failed/cancelled）。

### D4 task 事件与链式改动
注册表变更 → SSE `task` 事件（action: created|updated|terminal，payload=task 快照，含 delegate_id/type/status）。
新增事件类型需同步 sse 序列化/`from_payload`/SSEEvent 联合（未知类型 raise）+ resume 回放路径（链式清单同 core change）。

### D5 UI
可折叠"任务/进度"面板：标题/状态/当前活动摘要/时间。**stage 更新粒度 coarse**：注册表仅在 delegate start/end/中断等边界写 stage；**实时活动摘要由前端从 core 的 delegate 事件派生**（不另发 task 事件逐 delta 更新，避免刷屏与双份真相）。

## Risks / Trade-offs

- **看板范围膨胀** → 严格只读面板 + 工具最小集；不接独立任务服务。
- **plan 与 execution 语义混淆** → type 区分 + delegate_id 关联（D2）。
- **单 worker 进程内限制** → 与既有 streaming 状态一致，文档明示。
- **task 事件链式漏改** → 与 core 共享"链式改动清单"做法（sse/from_payload/LOG_PREFIXES/EventSpec + resume 测试）。

## Migration Plan

- 依赖 core change 先行（delegate_id 事件）；单 change 可回退提交。
- 冒烟：委托一次 + Task 工具建项，确认看板两条 type 可见、delegate 终态/超时原因正确、前端只读。

## Open Questions

- 无阻塞项（暴露范围已在 D3 定稿：仅主 agent 写、前端只读）。
