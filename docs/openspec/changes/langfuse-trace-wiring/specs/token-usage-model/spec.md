## MODIFIED Requirements

### Requirement: TokenUsage SHALL be a dataclass with consistent shape

The system SHALL define a `TokenUsage` dataclass with `prompt_tokens`, `completion_tokens`, and `total_tokens` fields to replace the two inconsistent dict shapes.

`TokenUsage` 的宿主模块 SHALL 同时承载其唯一的估算构造入口 `estimate_usage()` —— 该函数与 `TokenUsage` 是同一职责，不再单独占用另一个模块。

#### Scenario: LLM native token metadata maps to TokenUsage

- **WHEN** LLM 调用返回带 `input_tokens` / `output_tokens` 的 `usage_metadata`
- **THEN** 主 agent 推理点（`agent_model`）与委派子代理推理点（`fork_stream` 的 model turn 记录）SHALL 将其映射为 `TokenUsage(prompt_tokens=..., completion_tokens=..., total_tokens=...)`

> 原场景以 `stream_answer()` 为映射载体；该函数是零调用方的死代码，本变更删除它，映射载体改为上述现役路径。

#### Scenario: Estimated usage maps to same shape

- **WHEN** LLM 未提供 `usage_metadata` 而调用 `estimate_usage()`
- **THEN** 它 SHALL 返回 `TokenUsage(input, output, input+output)` —— 同一形状

#### Scenario: estimate_usage 与 TokenUsage 同属一个模块

- **WHEN** 查找 `TokenUsage` 的定义位置
- **THEN** 同一模块内可找到 `estimate_usage()` 的定义（本变更后不再存在 `rag/stream.py` 这一宿主）

### Requirement: TokenUsage's total_tokens shall always be correct

The `total_tokens` field SHALL always be the sum of `prompt_tokens + completion_tokens`.

#### Scenario: total_tokens 恒为两项之和

- **WHEN** 以任意 `prompt_tokens` / `completion_tokens` 构造或估算出一个 `TokenUsage` 实例
- **THEN** `usage.total_tokens` SHALL 等于两者之和
- **AND** `usage.get("total", 0)` 这类字典取值写法 SHALL NOT 出现在代码中

> 原场景以已不存在的 `generate_node` 为读取点。本变更另删除了两个 `total_tokens` 的消费者（`stream_answer` 内部、`LangfuseTracer.end_generation`），因此该不变量改为**直接断言 dataclass 属性**来验证，不再依赖某个特定调用点。
