## MODIFIED Requirements

### Requirement: abort 后清理与孤儿消息处理

系统 SHALL 按"user 到达即写、assistant 完成/中止时写"落库：user 消息（原 query、澄清答案）到达即同步写入 Redis 与关系型库（用户真实输入，abort 也保留，`created_at` 为请求发起时刻）；assistant 消息在完成时写完整到 Redis 与关系型库，abort 时有 token 写部分答案（标记 interrupted）**仅到关系型库**（Redis 历史只保留完整轮次，避免半截回答进入下一轮 prompt 上下文）。SSE 生成器静默收尾，不向已断开连接写 done 事件；done 事件由后台任务写入会话缓冲供续接消费。

#### Scenario: 部分生成后中止
- **WHEN** cancel 中止时已产出部分 token
- **THEN** 部分答案标记 interrupted 随 user 消息落库，前端历史完整

#### Scenario: 无产出中止
- **WHEN** cancel 中止时未产出任何 token
- **THEN** 仅 user 消息落库（到达即写已保证），不写入 assistant 记录
