# task-board Proposal

## Why

委派与后台执行对用户仍是"黑盒进度"：用户看不到子代理正在做什么、整体有哪些任务在跑。参照 claude-code 的 Task*Tool/TaskListV2 形态，提供主 agent 可用的 Task 工具与前端任务/进度看板，让"子代理正在做什么/整体进度"可见可跟踪。

## What Changes

- **Task 工具集**：create/get/list/update/output/stop 最小集，供主 agent 建任务、更新状态/依赖并跟踪委派与后台执行。**stop 语义**：置注册表条目为 cancelled，并对运行中的 delegate 尽力请求取消（接 ctx.abort_signal；执行协程配合取消）；非运行中条目仅置终态。
- **会话级任务注册表**：进程内、key 含 session_id，条目带 TTL（默认 30min）惰性清理；同一会话新 POST 保留既有任务直至 TTL。
- **执行自动登记**：delegate fork 启动自动登记、结束更新终态（done/failed/timeout+原因），与 Task 工具共用注册表——两类条目以 type/scope 区分（主 agent 计划项 vs 执行追踪），经 delegate_id 关联。
- **SSE task 事件 + 看板 UI**：注册表变更推前端（action + task 快照），可折叠"任务/进度"面板展示（只读），与回答内"分析过程"折叠区互补；另提供**任务快照读取接口**（会话维度）供刷新/切换会话后初始化看板。
- **stage 更新粒度（coarse）**：注册表仅在 coarse 边界写 stage（delegate start/end/中断）；**实时活动摘要由前端从 delegate 事件派生**——避免 task 事件逐 token 刷屏与"注册表 stage vs 前端过程区"双份真相。
- 依赖：delegate 事件携带 delegate_id（delegate-hardening-observability core change 提供）；stop 的 cancelled 与中断原因均取 core 的 `DelegateStopReason`。**顺序依赖（前后端与契约链同规则）**：core 先行，本 change 在 core 之上合并——共享文件链含 `deploy/nginx/html/chat.html`、`sse.py`/`from_payload`、`api_contract.md`、`data-flow.md`；避免并行覆盖。

## Capabilities

### New Capabilities
- `task-board`: Task 工具集（create/get/list/update 等）与任务/进度看板 UI，反映子代理与任务活动

### Modified Capabilities
- （无）

## Impact

- SSE：`task` 事件序列化 + `from_payload` 等链式更新（同 core 的链式改动清单）；新增 `GET /api/sessions/tasks` 快照接口（会话权限）
- 后端：会话级任务注册表（挂 streaming 侧进程内状态）、delegate_task 登记/终态、Task 工具集
- 前端 `deploy/nginx/html/chat.html`：task 事件 handler + 任务/进度看板折叠面板
- 测试与归属文档
