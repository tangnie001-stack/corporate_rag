# agent-loop-observability Specification

## Purpose
TBD - created by syncing change agentic-clarification. Update Purpose after sync.
## Requirements

### Requirement: 全流程节点日志可查

系统 SHALL 为流水线每个图节点（kb_router、classify（若保留）、agent 循环、format 等）记录进入与结束日志：节点名、trace_id、关键入参摘要、输出摘要、耗时。日志经 trace_id SHALL 可还原单次请求的完整节点执行链（节点顺序 + 各节点输入输出）。

#### Scenario: 按 trace_id 还原执行链
- **WHEN** 排查一次回答的生成过程
- **THEN** 按 trace_id 聚合日志可按执行顺序还原每个节点的进出与耗时

#### Scenario: 节点异常可定位
- **WHEN** 某节点执行异常
- **THEN** 日志含节点名与 trace_id，可定位到具体节点与请求

### Requirement: 工具调用日志

系统 SHALL 为 agent 循环中的每次工具调用（retrieve_kb、ask_user、escalate_to_human）输出一行结构化日志：iteration 序号、工具名、参数摘要、结果摘要、耗时、token 增量（如有）。日志 SHALL 足以在无 Langfuse 的情况下从文本还原"模型调了什么工具 → 得到什么 → 下一步决策"。

#### Scenario: 工具调用可追踪
- **WHEN** agent 循环中调用 retrieve_kb
- **THEN** 日志输出一行含 trace_id/iteration/tool=retrieve_kb/args 摘要/结果条数/耗时

#### Scenario: 循环序列可还原
- **WHEN** 排查一次多轮检索的 agent 回答
- **THEN** 按 trace_id 聚合日志可按 iteration 顺序还原完整工具调用序列

### Requirement: 护栏命中告警

系统 SHALL 在护栏触发时输出 warn 级日志并记录现场：`MAX_AGENT_ITERATIONS` 命中（含 query 与 iteration 轨迹）、`ASK_USER_TIMEOUT` 超时、`MAX_ASK_PER_TURN` 超限、abort 中止（含已耗 token 与当前 stage）。此类日志作为任务定义/工具设计异常的运营信号。

#### Scenario: 迭代上限命中告警
- **WHEN** agent 循环达 MAX_AGENT_ITERATIONS 强制收尾
- **THEN** 记录 warn 日志，含 query、iteration 轨迹、触发原因

### Requirement: 循环内 token 用量聚合

系统 SHALL 在 agent 循环内对每次 LLM 调用的 token 用量（复用 TokenUsage）进行聚合，turn 结束时记录循环总用量（prompt/completion/total）与调用次数，随现有日志输出。

#### Scenario: 多轮检索用量汇总
- **WHEN** agent 循环含 3 次 LLM 调用
- **THEN** turn 结束日志含 3 次调用的总 token 用量与调用次数
