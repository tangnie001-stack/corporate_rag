# request-abort Specification

## Purpose
TBD - created by syncing change agentic-clarification. Update Purpose after sync.
## Requirements

### Requirement: 断连即停（abort 语义）

系统 SHALL 在用户关闭页面/跳转导致浏览器断开时，中止正在执行的 agent 循环。abort 信号 SHALL 为 per-request 注入式（由 api 层创建，检测到断连时置位），agent 循环在每一步边界（LLM 调用前、工具执行前、ask_user 等待前）检查信号并中止。LLM 调用 SHALL 使用异步流（`llm.astream()`），使取消可传播，不得出现"取消后线程池继续跑 LLM"。

#### Scenario: 生成中关闭页面
- **WHEN** 用户在 agent 生成答案期间关闭页面
- **THEN** 请求取消，agent 循环在下一个边界中止，LLM 调用停止，不产生后续 token 消耗

#### Scenario: 检索阶段关闭页面
- **WHEN** 用户在检索/工具执行阶段关闭页面
- **THEN** 工具执行中止，agent 循环停止

### Requirement: ask_user 挂起绑定 abort

系统 SHALL 将 ask_user 的挂起 Future 绑定到 abort 信号；断连时信号置位，Future 立即以取消原因 resolve，挂起澄清注册表清理，不留残留等待。

#### Scenario: 澄清等待中断连
- **WHEN** 用户关闭页面而 ask_user 正等待答案
- **THEN** Future 被取消唤醒，注册表清理，无内存泄漏

### Requirement: abort 后清理与孤儿消息处理

系统 SHALL 按"user 到达即写、assistant 完成/中止时写"落库：user 消息（原 query、澄清答案）到达即写 Redis（用户真实输入，abort 也保留）；assistant 消息在完成时写完整、abort 时有 token 写部分答案（标记 interrupted）到 Redis 与 MySQL。SSE 生成器静默收尾，不向已断开连接写 done 事件。

#### Scenario: 部分生成后中止
- **WHEN** abort 时已产出部分 token
- **THEN** 部分答案标记 interrupted 随 user 消息落库，前端历史完整

#### Scenario: 无产出中止
- **WHEN** abort 时未产出任何 token
- **THEN** 仅 user 消息落库（到达即写已保证），不写入 assistant 记录

### Requirement: 同 session 并发防护

系统 SHALL 拒绝同一 session 的并发 `/chat/stream` 请求：per-session 锁（Redis `SETNX chat_lock:{session_id}` 带 TTL）在请求开始时获取、结束时 finally 释放；获取失败返回 409"当前会话正在处理中"。前端禁输入挡常见情况，锁为后端兜底（双 tab/异常重放）。

#### Scenario: 双 tab 并发被拒
- **WHEN** 同一 session 已有活跃 stream 时再次发起请求
- **THEN** 新请求返回 409，不启动第二个 graph 执行

#### Scenario: 锁异常释放
- **WHEN** 请求异常结束或客户端断连
- **THEN** finally 释放锁，下一次请求可正常获取
