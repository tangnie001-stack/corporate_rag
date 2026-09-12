# chat-message-model Delta（session-process-replay）

## ADDED Requirements

### Requirement: assistant 消息持久化 SHALL 携带过程事件与模型名

assistant 消息落库时 SHALL 同时写入 `process`（生成期采集的有序事件 JSON 数组，`[{seq, type, payload}, ...]`，MEDIUMTEXT NULL）与 `model_used`（本次实际模型名，VARCHAR(64) NULL）两列；`conversation_history` 表 SHALL 通过 ALTER TABLE 增加这两列，存量消息为 NULL 不回填。落库数据 SHALL 取自本次生成的私有事件日志（独立于 StreamingRunManager 缓冲，不受其 2000 帧截断与 300s TTL 影响），取消/超时路径 SHALL 落库到中断点为止的事件。

#### Scenario: 正常完成写入完整事件

- **WHEN** 一次生成正常完成执行 save_assistant_message
- **THEN** assistant 行 process 列 SHALL 为按到达顺序的完整事件 JSON 数组，model_used 列 SHALL 为实际模型名

#### Scenario: 取消路径写入部分事件

- **WHEN** 用户中止生成，partial 落库执行
- **THEN** process 列 SHALL 包含到中断点为止的已采集事件，且携带中断语义（与既有 interrupted 状态一致）

#### Scenario: 存量行为不变

- **WHEN** 读取或写入不涉及新列的既有消息路径（Redis 历史、user 消息）
- **THEN** 行为与变更前完全一致，新列不影响任何既有读写
