# sse-stream-resilience Specification

## Purpose
TBD - created by archiving change delegate-hardening-observability. Update Purpose after archive.
## Requirements
### Requirement: 主流空闲收流与任务存活解耦

主 POST 流（/chat/stream）的订阅 SHALL 在会话存在活跃生成任务时不因空闲收流；流终态由任务生命周期（done/error）提供。resume 端点（/api/sessions/events）无活跃任务时的空闲错误兜底 SHALL 保留。

#### Scenario: 活跃任务长静默不断流
- **WHEN** 后台生成任务仍在运行且期间无 SSE 事件
- **THEN** 主 POST 流保持连接，不按旧 180s 空闲策略收流

#### Scenario: resume 僵尸续接兜底
- **WHEN** 无活跃任务且 resume 端点持续无事件
- **THEN** 仍按空闲阈值返回续接超时错误（原语义保留）

### Requirement: 前端干净 EOF 自动续接

前端收到干净 EOF（流正常关闭）且本轮未收到终态（done/error）时，SHALL 按已消费最大 seq 调用 resume（lastSeq 续接），不得停留在无响应状态。

#### Scenario: EOF 未收终态触发恢复
- **WHEN** fetchStream 干净结束且本轮无 done/error
- **THEN** 前端自动发起 resume（after_seq=lastSeq）接回事件流

#### Scenario: 已收终态不重复续接
- **WHEN** 本轮已收到 done/error 终态
- **THEN** 干净 EOF 不触发续接（正常收尾）

### Requirement: 长任务期间保持流量

长生成任务（含长 fork）期间，系统 SHALL 通过任务/代理事件或心跳维持经 nginx（proxy_read_timeout 300s）的流量，避免代理按读超时断开。

#### Scenario: 长 fork 持续有事件
- **WHEN** fork 持续产生事件（含聚合后的心跳）
- **THEN** 主流经代理保持活性，不被 300s 读超时断开
