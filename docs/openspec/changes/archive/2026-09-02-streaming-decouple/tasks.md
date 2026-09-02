# streaming-decouple Tasks

## 1. M1 持久化时序调整

- [x] 1.1 MessageModel 增加 `status` 列（默认 complete，取值 complete/interrupted），`src/infra/db/models/chat.py` 与 `src/infra/db/mysql_db/models/chat.py` 两个副本同步
- [x] 1.2 编写 SQL 迁移脚本（`scripts/migrations/` 存档）并执行 `ALTER TABLE conversation_history ADD COLUMN status ...`，验证列存在
- [x] 1.3 `chat_repo.save_message` 透传 `status`（当前构造新 MessageModel 时丢弃 msg 的属性，需放行）
- [x] 1.4 `PersistenceService.save_messages` 支持拆分：user 单独写、assistant 带 status 写
- [x] 1.5 `/api/chat/stream`（**保持 GET**，POST 迁移推迟到 3.5 与前端传输层一同落地）流程改为：取锁 → 创建 session（save_session 提前）→ user 落库（Redis 历史 + MySQL，**同步 await 写入成功后再启动生成**，created_at=请求时刻）→ 开始流式
- [x] 1.6 `_persist_conversation` 瘦身为"仅流结束后写 assistant（`status=complete`）"，移除 user/session 写入（已提前）
- [x] 1.7 ask_user 超时文案由 `"Error: 等待用户回答超时"` 改为引导推荐文案（`src/config/const.py` `ASK_USER_TIMEOUT_TEXT`），同步相关测试断言
- [x] 1.8 新增回归测试：同轮 user 的 created_at 早于 assistant，读取顺序 user 在前
- [x] 1.9 `MessageItem` 增加 `status` 字段（`src/api/model/response.py` + `app_service.get_messages` 透传），同步 `docs/agents/api_contract.md` 与受影响测试断言

## 2. M2 生成进后台任务 + seq 缓冲 + 断连只停推送

- [x] 2.1 新建 `src/chat/streaming.py`：`StreamingRunManager`（`_session_tasks` 注册表 + 强引用集合 + done_callback 注销）
- [x] 2.2 事件缓冲实现：per-session `(seq, type, payload)` 列表，seq 递增，cap 2000 / 终态后 TTL 5min 惰性清理，新一轮 `POST /api/chat/stream` 清空
- [x] 2.3 SSE 事件序列化契约：9 种事件类补 `type` 属性 + `payload_for_buffer()`，实现 `sse.from_payload()`，round-trip 测试（`to_sse(from_payload(e.type, e.payload_for_buffer())) == to_sse(e)`）
- [x] 2.4 生产者拆分：后台任务内迭代 `graph.astream_events()` + clarify_channel，产出带 seq 事件写入缓冲（拆出 `_dual_stream` 的 Task A 角色）；`RequestContext`（含 trace_id）与 `session_id → abort_signal` 映射在任务内创建/登记
- [x] 2.5 消费者拆分：SSE 生成器从缓冲读事件推送（拆出 `_dual_stream` 的 Task B 角色）；`except GeneratorExit` 只退出消费循环，不取消后台任务
- [x] 2.6 abort 触发器反转：生产者 finally 不再因 SSE 断开置位 abort_signal；abort 仅由 cancel 端点经映射置位；`stream_chat` 启动任务处顺序约束：先 `get_history_async()` 再 `add_message_async("user")` 再启动任务（当前 query 不进历史，配测试断言）
- [x] 2.7 并发防护：POST 先查进程内任务注册表（存在活跃任务 → 409）再获取 Redis 锁；注册表注销与锁释放均在后台任务完成时（done_callback），Redis 锁 TTL 仅作兜底
- [x] 2.8 删除 `_persist_conversation`，assistant 收尾（complete / interrupted / cancelled）并入后台任务 coroutine（含重试）
- [x] 2.9 应用启动时清空 `chat_lock:*` 键（重启后进程内无任务，锁必然残留，删除安全）

## 3. M3 续接 / 状态 / 取消接口 + 前端

- [x] 3.1 新增 `GET /api/sessions/events`（SSE resume）：回放缓冲中 `seq>after_seq` 事件 + tail 实时，遇 done/error 收尾；无缓冲返回 done；**tail 空闲超时 180s → error（"续传超时，请刷新页面"）**；事件名与 payload 格式与实时流完全一致（含 done 的 trace_id）；同步 `docs/agents/api_contract.md`
- [x] 3.2 新增 `GET /api/sessions/task-status`：generating（缓冲有无终态）/ completed（assistant 存在）/ idle（无缓冲无 assistant）；同步 `docs/agents/api_contract.md`
- [x] 3.3 新增 `POST /api/sessions/cancel`：从 `session_id → abort_signal` 映射取信号置位；无活跃任务返回 `no_active_task`；同步 `docs/agents/api_contract.md`
- [x] 3.4 三接口复用 cookie 鉴权 + session 归属校验（照抄 `sessions.py` `get_session_by_id` + user_id 比对，越权 404）
- [x] 3.5 传输层 + POST 迁移：`/api/chat/stream` 由 GET 改 POST（新增 `ChatStreamRequest` 请求体）；前端新增 `fetchStream` 助手（`fetch POST /api/chat/stream` + `getReader` + SSE 帧解析，处理跨包半帧），`startSSE` 改消费该流，事件渲染逻辑复用
- [x] 3.6 前端：记录同页会话 lastSeq；断线（网络抖动）不重放启动，改调 `GET /api/sessions/events?after_seq=lastSeq` 续接（指数退避，上限 3 次）；刷新后先拉 MySQL 历史，再按 status 决定 resume（generating 时从 seq 0 回放当前轮）
- [x] 3.7 前端：停止按钮 → `POST /api/sessions/cancel`；生成中禁输入 + 409 提示
- [x] 3.8 前端：`status=interrupted` 的部分回答展示（标记中断），历史回放不重复渲染（依赖缓冲当前轮生命周期）
- [x] 3.9 澄清路径适配：resume 到 ask_user 事件时进入澄清输入态（复用 `renderComposer`），过期澄清（buffer 已含 done/cancelled）不渲染；后端澄清链路集成验证（后台任务等澄清 + resume 回放 ask_user + `POST /chat/clarify-answer` resolve + 任务续跑）

## 4. M4 P2 细节与测试收尾

- [x] 4.1 删除会话 / clear_history 时：取消该 session 正在运行的任务（置位 abort_signal）、释放锁、清理缓冲与任务注册表，防止旧任务写回已删除会话
- [x] 4.2 abstention（拒答）与 deep_thinking（reasoning）事件纳入缓冲与 resume 回放
- [x] 4.3 后台任务 trace_id 显式传递验证（日志可按 trace 关联）
- [x] 4.4 存量测试适配：request-abort / dual_stream / agent_service / test_sessions 断言更新
- [x] 4.5 新增 streaming-run 测试：缓冲回放、cancel 语义、TTL 清理、status 三态判定、越权 404
- [x] 4.6 质量门禁：`pytest tests/ -v` 全过、`ruff check .` 无错误、`pyright src/` 不新增 error
