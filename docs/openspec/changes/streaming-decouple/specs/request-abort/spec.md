# request-abort Specification (Delta)

## MODIFIED Requirements

### Requirement: 生成与连接解耦，仅 cancel 中止生成

系统 SHALL 将 agent 循环置于与 SSE 连接解耦的后台任务中运行；用户关闭页面/刷新导致浏览器断开时，SSE 生成器只停止向已断开连接推送事件，不得中止后台生成任务。abort 信号 SHALL 仅由显式 cancel 接口（`POST /api/sessions/cancel`）置位；agent 循环在每一步边界（LLM 调用前、工具执行前、ask_user 等待前）检查信号并中止。LLM 调用 SHALL 使用异步流（`llm.astream()`），使取消可传播，不得出现"取消后线程池继续跑 LLM"。

#### Scenario: 生成中刷新/关闭页面
- **WHEN** 用户在 agent 生成答案期间刷新页面或关闭页面
- **THEN** SSE 推送停止，后台生成任务继续运行，事件写入会话缓冲，供刷新后断点续接

#### Scenario: 用户点击停止按钮
- **WHEN** 用户在生成期间点击「停止」按钮
- **THEN** cancel 接口置位 abort 信号，agent 循环在下一个边界中止，LLM 调用停止，不产生后续 token 消耗

#### Scenario: 检索阶段点击停止
- **WHEN** 用户在检索/工具执行阶段点击「停止」
- **THEN** 工具执行中止，agent 循环停止

### Requirement: ask_user 挂起绑定 abort 与超时

系统 SHALL 将 ask_user 的挂起 Future 绑定到 abort 信号；仅当 cancel 置位信号或等待超时（`_ask_user_timeout`）时，Future 以取消原因 resolve，挂起澄清注册表清理，不留残留等待。客户端断开不触发 resolve——用户刷新后经续接接口仍可看到澄清问题并回答。

#### Scenario: 澄清等待中点击停止
- **WHEN** 用户点击「停止」而 ask_user 正等待答案
- **THEN** Future 被取消唤醒，注册表清理，无内存泄漏

#### Scenario: 澄清等待中刷新页面
- **WHEN** 用户刷新页面而 ask_user 正等待答案
- **THEN** 后台任务继续等待（未超时），刷新后前端经续接接口显示澄清问题，用户可继续回答

### Requirement: abort 后清理与孤儿消息处理

系统 SHALL 按"user 到达即写、assistant 完成/中止时写"落库：user 消息（原 query、澄清答案）到达即同步写入 Redis 与 MySQL（用户真实输入，abort 也保留，`created_at` 为请求发起时刻）；assistant 消息在完成时写完整到 Redis 与 MySQL，abort 时有 token 写部分答案（标记 interrupted）**仅到 MySQL**（Redis 历史只保留完整轮次，避免半截回答进入下一轮 prompt 上下文）。SSE 生成器静默收尾，不向已断开连接写 done 事件；done 事件由后台任务写入会话缓冲供续接消费。

#### Scenario: 部分生成后中止
- **WHEN** cancel 中止时已产出部分 token
- **THEN** 部分答案标记 interrupted 随 user 消息落库，前端历史完整

#### Scenario: 无产出中止
- **WHEN** cancel 中止时未产出任何 token
- **THEN** 仅 user 消息落库（到达即写已保证），不写入 assistant 记录

### Requirement: 同 session 并发防护

系统 SHALL 拒绝同一 session 的并发 `/chat/stream` 请求：请求开始时先查进程内任务注册表（`_session_tasks` 存在未完成任务即拒绝）并获取 per-session 锁（Redis `SETNX chat_lock:{session_id}` 带 TTL 兜底），两者均在后台生成任务完成时 finally 释放（而非 SSE 连接结束时）；拒绝时返回 409"当前会话正在处理中"。Redis 锁 TTL 过期不构成竞态——进程内注册表始终准确，TTL 仅作兜底上限。前端禁输入挡常见情况，注册表与锁为后端兜底（双 tab/异常重放）。

#### Scenario: 双 tab 并发被拒
- **WHEN** 同一 session 已有活跃生成任务时再次发起请求
- **THEN** 新请求返回 409，不启动第二个 graph 执行

#### Scenario: 锁异常释放
- **WHEN** 生成任务异常结束或显式取消
- **THEN** finally 释放锁，下一次请求可正常获取
