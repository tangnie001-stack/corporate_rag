# streaming-decouple Design

## Context

当前 `/api/chat/stream`（SSE）内联驱动 LangGraph 生成：客户端断开时 `_dual_stream` 的 finally 置位 abort_signal 并取消事件源任务，生成随连接中止。持久化（`_persist_conversation`）在流正常结束后一次性写 user+assistant 到 MySQL，导致：(1) 刷新即丢轮次、Redis 留孤儿 user 消息；(2) user/assistant 同秒平级、`ORDER BY created_at` 顺序随机。

两个参考项目的调研结论：
- **deepseek-harness**：生成与连接解耦（host 常驻 agent），事件带 seq，断线重连后 resync，取消走独立 `session.cancel`，事件增量落盘。
- **financial_rag-main**：FastAPI 落地版——后台 `asyncio.Task` + 进程内缓冲 + `last_seq` 续传 + `/cancel` 接口，user 请求开始落库。

本项目约束：生产环境单 worker（已决策，见 CLAUDE.md / defensive-patterns.md），流式状态在进程内。

## Goals / Non-Goals

**Goals:**
- 生成与 SSE 连接解耦：刷新/关闭页面只停推送，生成继续
- 刷新后可续接当前轮的流式输出（回放 + tail）
- 中止仅由显式 cancel 触发
- 消息落库时序正确：user 请求开始即写、assistant 完成/中止时写
- 单 worker 部署，状态全在进程内

**Non-Goals:**
- 多 worker / Redis 共享状态（prod 改单 worker，见决策 D1）
- 增量写穿落盘（write-behind，记录为增强项）
- 修复 alembic 迁移基建（登记为独立技术债）
- 线程池 offload CPU 阻塞点（jieba/ChromaDB）——登记为独立性能优化，不在本 change 范围
- 前端大规模重写（在现有 chat.html 上增量改）

## Decisions

### D1. 单 worker 部署，进程内状态
**选择**：生产环境 uvicorn 单 worker（改 `--workers 4` → 1），StreamingRunManager 的任务注册表与事件缓冲放进程内内存。
**理由**：流式聊天为 I/O 密集，单 worker 事件循环可挂大量连接；多 worker 下续接/取消需 Redis 共享与跨进程信号协调（两个参考项目均未实现），复杂度不匹配当前负载。
**备选**：多 worker + Redis（buffer 镜像 + cancel flag + ask_user 转发）——预留接口插槽，后续可加；每用户 host 进程（deepseek-harness 形态）——为个人工具设计，多租户 Web 不适用，排除。

### D2. 生成进后台任务，SSE 只订阅
**选择**：`POST /api/chat/stream`（由现有 GET 改为 POST）取锁后创建后台 asyncio 任务（`StreamingRunManager` 持有强引用），任务内建 `RequestContext`（含 trace_id）跑图；SSE 生成器只消费事件并推送。断连（GeneratorExit）只停止推送。
**理由**：与 deepseek-harness / financial_rag-main 同构；断连不再触发 abort。
**备选**：维持内联生成 + 断连续传靠 nginx 缓冲——无法跨连接续接，排除。

### D3. 事件带 seq + 按"当前轮"生命周期管理的缓冲
**选择**：每事件 `(seq, type, payload)`，seq per-session 递增；缓冲在新一轮 `POST` 时清空，cap 2000 / 终态后 TTL 5min；done/error 也入缓冲。
**理由**：已完成轮次内容在 MySQL，未完成轮次只在缓冲——两边不重叠，前端刷新续接从 seq 0 回放当前轮即可，**无需 seq 去重机制**（G6）。cap 2000 覆盖长回答的流式事件数（token 级事件可达数百条），超长回答丢头为已知限制。
**备选**：持久化 lastSeq 跨刷新续接——页面状态已丢，无收益；buffer 跨轮保留 + seq 去重——引入重复渲染风险，排除。

### D4. 仅 cancel 触发中止
**选择**：新增 `POST /api/sessions/cancel`，置位任务级 abort_signal；agent 循环在步骤边界检查（复用现有 abort 传播机制）。`StreamingRunManager` 维护 `session_id → abort_signal` 映射（任务启动时登记、完成时注销），cancel 端点从映射取信号置位。`_dual_stream` 的 finally 不再因 SSE 断开置位 abort。
**理由**：符合 request-abort 规格反转后的语义（仅 cancel 才停）；映射让 cancel 端点能触达进程内任务的信号。
**备选**：`task.cancel()` 硬取消——立即但破坏状态清理路径，且多 worker 下不可用；维持断连即 abort——违背产品目标，排除。

### D10. 生产者 / 消费者结构分离
**选择**：将现 `_dual_stream` 的"Task A 迭代 graph 事件"与"Task B 消费转 SSE"拆成两个独立单元：**生产者**（后台任务内迭代 graph.astream_events + clarify_channel，产出带 seq 事件写入缓冲）与**消费者**（SSE 生成器从缓冲读事件推送）。两者通过带 seq 的缓冲连接，不再共享一个生成器生命周期——消费者断开只退出消费循环，生产者继续。
**理由**：`_dual_stream` 的 finally 会连带清理事件源，不拆分则"断连不 abort 后台任务"无法成立。financial_rag-main 的 `_background_agent_stream` + `event_generator` 即此结构。
**备选**：维持共享生成器——断连即触发 finally 清理，排除。

### D5. 持久化时序
**选择**：user 消息请求开始时**同步**写入 Redis 与 MySQL（`created_at=NOW()`=请求时刻，session 创建同步提前，写入成功后才启动生成——作为请求硬前提）；assistant 完成写完整到 Redis 与 MySQL（`status=complete`）、取消/出错有 token 写部分（`status=interrupted`，**仅写 MySQL**，Redis 历史只保留完整轮次，避免半截回答进入下一轮 prompt 上下文）、无 token 只留 user。`conversation_history` 加 `status` 列。
**理由**：写入时机差异使 user/assistant 的 created_at 天然相隔生成时长，**解决同秒平级无需加 seq 列**（G7）；user 同步写保证"问题必然留下"（正是要修的刷新丢问题 bug）；Redis 仅完整轮保持 prompt 上下文干净。
**备选**：加 seq 自增列 / DATETIME(6)——为罕见瞬时同秒（1s 内取消）过度设计，排除；user 异步 best-effort 写——存在"请求开始了但问题没写进去"的不一致窗口，排除。

### D6. ask_user 澄清：断连不 resolve，超时走推荐
**选择**：ask_user Future 仅由 abort_signal（cancel）或 120s 超时 resolve；超时返回引导文案（"因超时未填写内容，请基于已有上下文给出推荐方案"）作为工具结果给 LLM，由 LLM 基于上下文给推荐。
**理由**：断连不 resolve 使"刷新后回来还能答"成立；超时文案保证任务有界且回答仍有用。
**备选**：断连即 resolve（原行为）——刷新丢澄清；无限等待——任务/锁挂死，排除。

### D7. 并发防护：进程内任务注册表为准 + Redis 锁兜底
**选择**：`POST /api/chat/stream` 时先查进程内任务注册表（`_session_tasks` 存在未完成任务 → 409），再获取 Redis 锁（`SETNX chat_lock:{session_id}`，兜底跨进程与进程重启）；锁在后台任务完成时 finally 释放。Redis 锁 TTL 过期不构成竞态——注册表在进程内始终准确，TTL 只是兜底上限。
**理由**：单 worker 下进程内注册表是"是否在生成"的唯一准确判据（done_callback 保证注销），Redis 锁 TTL（180s）可能小于 ask_user 120s + 生成时长，但注册表挡在锁之前，TTL 过期无害。避免 heartbeat 复杂度。
**备选**：心跳续期锁 TTL——为单 worker 下本就精确的注册表修一个已不存在的竞态，且续期与释放存在时序 bug 面，推迟到 M5（多 worker）时作为 Redis 锁主防线的依赖；SSE 断开即释放——刷新后立即发新问题会双生成，排除。

### D8. 鉴权复用现有机制
**选择**：resume/status/cancel 均放 `/api/sessions/` 下，自动走现有 cookie 鉴权中间件；会话归属校验照抄 `sessions.py` 的 `get_session_by_id` + user_id 比对模式。
**理由**：cookie 鉴权下同源请求（fetch/EventSource）自动携带凭据，无需新机制。

### D9. 传输层：fetch+getReader，POST 启动 + resume 续接
**选择**：前端弃用原生 EventSource，改为 `fetch POST /api/chat/stream` + `response.body.getReader()` 手动解析 SSE 帧；断线（网络抖动）不重放启动请求，改调 `GET /api/sessions/events?after_seq=lastSeq` 续接（指数退避，上限 3 次）；页面刷新走历史加载 + status 判断 + resume（从 0 回放当前轮）。
**理由**：原生 EventSource 断线自动重连会重放"启动生成"请求（且只能 GET），与"启动/订阅解耦"目标冲突；fetch 可完全控制重连，且 POST 语义正确。事件渲染逻辑复用，仅传输层替换。
**备选**：EventSource + `onerror` 关闭自动重连——仍受 GET 限制且重连控制别扭，排除。

## Risks / Trade-offs

- **进程死亡/部署重启丢缓冲与任务** → resume 退化为历史加载（问题可见、无回答，用户重问）；status 返回 `idle` 而非 `generating`，避免前端死转圈。write-behind 作为后续增强。
- **瞬时取消同秒平级**（发问后 1s 内点停止，interrupted 回答与 user 同秒）→ 轻微乱序，可接受；如将来重视补 seq 列。
- **后台任务异常**（LLM 错误/超时）→ error 事件入缓冲 + 有 token 落 interrupted；无 token 只留 user。
- **锁被长等待占用**（ask_user 120s + 生成时长）→ 并发防护以进程内注册表为准，Redis 锁 TTL 过期无害（D7）。
- **ask_user 跨 worker 不可用** → 单 worker 部署下不成立；若未来多 worker 需 Redis 转发（登记为 M5）。
- **存量测试断言变更**（request-abort/dual_stream/agent_service）→ tasks 中列为显式适配项。

## Migration Plan

1. M1：持久化时序调整（user 同步落库、session 创建提前、assistant 完成/中止时写 + `status` 列、`_persist_conversation` 瘦身为仅写 assistant）
2. M2：生成进后台任务 + seq 缓冲 + 断连只停推送（abort 触发器改为仅 cancel；删除 `_persist_conversation`，assistant 收尾并入后台任务；启动时清空 `chat_lock:*`）
3. M3：resume/status/cancel 接口 + 前端续接 + 停止按钮（含 resume 空闲超时、clarify 澄清路径适配、MessageItem `status` 字段）
4. M4：P2 细节（删除会话清理缓冲/任务、abstention/reasoning 覆盖、trace_id 传递、存量测试适配）

回滚：M1/M2 为代码变更，git revert 即可；M3 涉及 schema 无破坏性变更（status 列可保留）；部署单 worker 由 compose 配置控制。

## Open Questions

- 停止按钮触发后，部分回答是否立即落库并刷新历史展示（与 M3 前端细节联动）。
