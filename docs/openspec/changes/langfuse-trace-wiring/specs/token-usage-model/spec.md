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

- **WHEN** 经由 `estimate_usage()` 或主 agent 推理点的 usage 映射得到一个 `TokenUsage` 实例
- **THEN** `usage.total_tokens` SHALL 等于 `usage.prompt_tokens + usage.completion_tokens`
- **AND** `usage.get("total", 0)` 这类字典取值写法 SHALL NOT 出现在代码中

> `TokenUsage` 是**普通 dataclass**、不做字段间推导（`total_tokens` 默认 `0`），所以该不变量由**构造点**保证：构造时必须显式传入与两项之和相等的 `total_tokens`。故本场景的断言对象是"**经由既有构造入口产出的实例**"，而不是"任意手工构造的实例" —— 后者可以为假且不代表任何真实路径。
>
> 原场景以已不存在的 `generate_node` 为读取点。本变更另删除了两个 `total_tokens` 的构造/消费点（`stream_answer` 内部、`LangfuseTracer.end_generation`），存留的构造入口见 `estimate_usage()`。
