## ADDED Requirements

### Requirement: LLM 调用信息写入日志
系统 SHALL 通过自定义 `LoggingCallbackHandler` 将 LLM 调用信息写入 Loguru 日志。
- SHALL 覆盖 `on_llm_start` 事件，记录模型名称和 messages 摘要
- SHALL 覆盖 `on_chunk` 事件，记录首 token 到达延迟
- SHALL 覆盖 `on_llm_end` 事件，记录完成时间、输出长度、token 用量（从 LLMResult 读取）
- SHALL 覆盖 `on_llm_error` 事件，记录异常信息和调用失败次数
- SHALL 始终生效，不受 `LANGFUSE_ENABLE` 控制

#### Scenario: LLM 调用开始记录日志
- **WHEN** `llm.stream()` 开始调用
- **THEN** 日志记录模型名称和 messages 条数+总字符数
- **THEN** 日志级别为 INFO

#### Scenario: 首 token 延迟记录
- **WHEN** `llm.stream()` 返回第一个 chunk
- **THEN** 日志记录首 token 延迟（毫秒）
- **THEN** 日志级别为 INFO

#### Scenario: LLM 调用完成记录日志
- **WHEN** `llm.stream()` 正常结束
- **THEN** 日志记录总延迟（毫秒）、输出总字符数、prompt_tokens、completion_tokens、total_tokens
- **THEN** 日志级别为 INFO

#### Scenario: LLM 调用失败记录日志
- **WHEN** `llm.stream()` 抛出异常
- **THEN** 日志记录异常类型和错误信息
- **THEN** 日志级别为 WARNING

### Requirement: LoggingCallbackHandler 传入方式
`LoggingCallbackHandler` SHALL 通过 `llm.stream(config={"callbacks": [...]})` 参数注入。
- SHALL 与 `LangchainCallbackHandler` 以数组形式同时传入
- SHALL 两个 handler 互不耦合，各自独立处理事件
- SHALL 日志 handler 始终存在，Langfuse handler 可能为 None

#### Scenario: 双 handler 同时生效
- **WHEN** `LANGFUSE_ENABLE=true` 且 `llm.stream()` 被调用
- **THEN** callbacks 数组中包含 langfuse handler 和 logging handler
- **THEN** 两个 handler 各自记录自己的内容，互不影响

#### Scenario: 仅日志 handler 生效
- **WHEN** `LANGFUSE_ENABLE=false` 且 `llm.stream()` 被调用
- **THEN** callbacks 数组中仅包含 logging handler
- **THEN** 日志正常记录，无 Langfuse 调用
