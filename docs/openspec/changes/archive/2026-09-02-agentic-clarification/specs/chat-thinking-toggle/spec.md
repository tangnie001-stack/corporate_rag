## ADDED Requirements

### Requirement: 前端深度思考开关

系统 SHALL 在主聊天输入区提供"深度思考"开关（默认不选中）；选中状态随 `/api/chat/stream` 请求以 `deep_thinking` 查询参数传递给后端（`true`/`false`）。

#### Scenario: 默认不选中

- **WHEN** 用户未勾选"深度思考"
- **THEN** 请求携带 `deep_thinking=false`，agent 主 LLM 以非思考模式调用（`enable_thinking=false`）

#### Scenario: 选中开启思考

- **WHEN** 用户勾选"深度思考"
- **THEN** 请求携带 `deep_thinking=true`，agent 主 LLM 以思考模式调用（`enable_thinking=true`）

### Requirement: 后端透传并控制 LLM 思考模式

系统 SHALL 在 `/api/chat/stream` 接受 `deep_thinking` 查询参数（默认 `false`），透传至 agent 循环，并在 LLM 调用时通过 `extra_body` 设置 `enable_thinking`（与参数取值一致）；默认态显式传 `false`（qwen3.7-flash 混合思考模式默认开启，必须显式关闭）。

#### Scenario: 思考模式生效

- **WHEN** `deep_thinking=true` 且 agent 调用 LLM
- **THEN** 请求携带 `enable_thinking=true`，模型输出思考内容（`reasoning_content`）

#### Scenario: 非思考模式生效

- **WHEN** `deep_thinking=false`（默认）
- **THEN** 请求携带 `enable_thinking=false`，模型直接输出正文

### Requirement: 思考过程不展示

系统 SHALL 不向前端展示思考过程——`reasoning_content` 经 langchain-openai 解析后不进入 `content` 或 `additional_kwargs`，深度思考仅影响回答质量，不改变 SSE 事件契约。

#### Scenario: 开启思考时前端仅见正文

- **WHEN** 深度思考开启且模型产出思考内容
- **THEN** 前端只收到最终正文 token 流（status/token/citation/done），不展示思考文本
