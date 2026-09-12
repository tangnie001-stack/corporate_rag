## 1. 会话级任务注册表

- [ ] 1.1 注册表（key 含 session_id；type=plan|execution；task_id/title/status/stage/summary/delegate_id/依赖/时间；execution 的 task_id=delegate_id；TTL 30min 惰性清理；新 POST 不清表），挂 streaming 侧进程内状态
- [ ] 1.2 delegate fork 启动自动登记 execution（task_id=delegate_id）、结束更新终态（done/failed/timeout+原因）；stage 仅在 coarse 边界（start/end/中断）更新，不做逐 delta 写入
- [ ] 1.3 单测：生命周期/TTL/跨轮保留、delegate 登记与终态、plan/execution 互不覆盖
- [ ] 1.4 任务快照接口 `GET /api/sessions/tasks?session_id=`（会话权限，同 sessions/events）+ 单测（有任务返回快照/无任务返回空）

## 2. Task 工具与事件链

- [ ] 2.1 Task 工具集（create/get/list/update/output/stop）读写注册表，仅主 agent 可见，经 ctx.session_id 定位；**stop 收敛语义**（design D3）：不 set ctx.abort_signal，仅对非终态条目置 cancelled（终态 action=terminal）；运行中 delegate 的真实取消走 cancel 端点（delegate 侧响应同一 ctx.abort_signal 收敛为中断 cancelled）
- [ ] 2.2 SSE `task` 事件序列化 + `from_payload`/SSEEvent 联合更新（未知类型 raise）+ resume 回放测试（与 core 链式清单同做法）
- [ ] 2.3 单测：Task 工具 CRUD、stop 的 cancelled/取消路径、task 事件 action 语义

## 3. 前端看板 UI

- [ ] 3.0 前置：实现前先读设计稿 `docs/design/pages/chat-delegate-progress-2026-09-07.md`，采用 `/frontend-design` skill 产出；在 core 的 chat.html 改动之上合并；同步更新设计文档（防腐）

- [ ] 3.1 chat.html：`task` 事件 handler + 只读可折叠"任务/进度"面板（按 type 分组，execution 携带 delegate_id 可关联过程区）；**实时活动摘要由前端从 core 的 delegate 事件派生**——**在 core change 对同一文件的改动之上合并**
- [ ] 3.2 进入会话/刷新时经 `GET /api/sessions/tasks` 拉取快照初始化看板
- [ ] 3.2 playwright 冒烟：委托一次 + Task 建项，确认两类条目、终态/超时原因、前端只读

## 4. 文档与收尾

- [ ] 4.1 归属文档同步 api_contract.md（task-board 契约增量）：
  - 2.4 会话管理新增小节 `GET /api/sessions/tasks?session_id=`（任务快照；权限同 sessions/events；data 为任务列表）
  - SSE 事件表（2.3.1）新增 `task` 事件行：`action=created|updated|terminal`、payload task（type=plan|execution、delegate_id、status、stage/summary）
  - logging-rules.md（如需登记事件）/ data-flow.md / glossary 登记术语：task type=plan|execution、DelegateStopReason（cancelled 等，core 已登记则此处引用）
- [ ] 4.2 openspec validate 通过；质量门禁（pytest/ruff/pyright）全绿
