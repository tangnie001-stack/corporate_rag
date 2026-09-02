# streaming-decouple Proposal

## Why

当前流式问答中 SSE 连接与 LLM 生成强绑定：用户刷新/关闭页面会 abort 生成，本轮问答丢失、Redis 历史留下孤儿 user 消息、MySQL 未落库。产品需要"刷新不中断输出、只有点停止才停、刷新后可续接"。同时现有持久化在流结束时一次性写 user+assistant，导致 created_at 同秒平级、历史顺序错乱。

## What Changes

- 生成与连接解耦：agent 循环移入后台 asyncio 任务，SSE 仅订阅推送；断连只停推送，不中止生成
- `POST /api/chat/stream`（由现有 GET 改为 POST，body 传 query/kb_id）启动生成并返回 SSE 流；前端传输层弃用原生 EventSource，改用 `fetch+getReader` 手动解析 SSE（可控断线重连）
- 事件携带 seq，per-session 事件缓冲按当前轮生命周期管理，提供断点续接（回放 + tail）
- 新增 `GET /api/sessions/events`（resume，断线/刷新后续接）、`GET /api/sessions/task-status`、`POST /api/sessions/cancel` 接口；中止仅由 cancel 触发
- 持久化时序调整：user 消息请求开始即写（Redis+MySQL，created_at=请求时刻）；assistant 完成写完整、取消/出错有 token 写部分（标记 interrupted）
- ask_user 澄清：断连不 resolve（刷新后仍可回答），120s 超时返回引导文案让 LLM 基于上下文给推荐
- 并发防护：`POST /api/chat/stream` 先查进程内任务注册表（活跃任务即 409）再取 Redis 锁，注册表注销与锁释放均在后台生成任务完成时（而非 SSE 连接结束）
- `conversation_history` 增加 `status` 列（complete/interrupted）
- 前端：刷新后 status 判断 + 续接当前轮、停止按钮、生成中禁输入
- 部署形态定为单 worker（进程内状态），已记录于 CLAUDE.md 与 defensive-patterns.md

## Capabilities

### New Capabilities
- `streaming-run`: 后台生成任务管理、per-session seq 事件缓冲与生命周期、resume/status/cancel 接口契约

### Modified Capabilities
- `request-abort`: 中止触发器从"断连"反转为"仅 cancel"；ask_user 挂起绑定调整（断连不 resolve、超时兜底）；会话锁释放时机改为任务完成时；user 消息持久化时机改为请求开始即写（Redis+MySQL）
- `agent-service`: 生成在后台任务中执行、事件携带 seq 写入缓冲、SSE 订阅缓冲推送

## Impact

- **代码**：`src/chat/streaming.py`（新增）、`src/api/chat.py`、`src/api/sessions.py`、`src/services/agent_service.py`、`src/chat/manager.py`、`src/chat/persistence.py`、`src/agents/tools/ask_tools.py`、`src/config/const.py`、`src/infra/db/models/chat.py`、前端 `deploy/nginx/html/chat.html`
- **数据**：`conversation_history` 增加 `status` 列，手工 ALTER + SQL 存档（alembic 基建未启用，修复登记为独立技术债）
- **部署**：生产环境单 worker（已决策并落档）
- **测试**：存量 request-abort / dual_stream / agent_service 测试适配；新增 streaming-run 测试
