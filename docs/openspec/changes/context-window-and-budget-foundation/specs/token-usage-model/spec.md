## ADDED Requirements

### Requirement: Token 计数 SHALL 使用项目统一分词器

系统的 token 计数 SHALL 通过项目统一的分词器实现（`tiktoken`，已为现有依赖）。`estimate_usage()` 与上下文/历史预算的计数 SHALL 复用同一计数入口，SHALL NOT 使用 `len(content) // 2` 之类的字符数粗估。

#### Scenario: estimate_usage 使用分词器计数

- **WHEN** LLM 未提供 `usage_metadata` 而以 `estimate_usage()` 兜底
- **THEN** 其 token 数 SHALL 由分词器对文本计数得出
- **AND** 返回的 `TokenUsage` 形状与 `total_tokens` 不变量（= `prompt_tokens + completion_tokens`）SHALL 保持不变

#### Scenario: 历史/上下文预算复用同一计数入口

- **WHEN** 计算历史窗口或上下文的 token 用量
- **THEN** SHALL 调用同一计数入口
- **AND** 代码中 SHALL NOT 存在内联的字符数算术粗估作为计数依据
