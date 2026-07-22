## ADDED Requirements

### Requirement: CLI 模式自动生成 trace_id
所有 CLI 入口的日志行包含 `trace_<uuid>` 格式的 trace_id，与 API 模式相同的日志格式。

#### Scenario: eval_ragas.py 日志带 trace_id
- **WHEN** 运行 `python -m src.cli.eval_ragas ...`
- **THEN** 每行日志的 `{extra[trace_id]}` 字段为 `trace_<uuid>` 格式

#### Scenario: check_retrieval.py 日志带 trace_id
- **WHEN** 运行 `python -m src.cli.check_retrieval ...`
- **THEN** 每行日志的 `{extra[trace_id]}` 字段为 `trace_<uuid>` 格式

### Requirement: API 模式不受影响
自动生成的 trace_id 不应覆盖 API 模式中由 middleware 注入的 trace_id。

#### Scenario: API trace_id 优先级
- **WHEN** API 请求到达，trace_id_middleware 通过 `X-Trace-ID` 请求头设入 ContextVar
- **THEN** logging patcher 使用 middleware 注入的值，而非自动生成的值
