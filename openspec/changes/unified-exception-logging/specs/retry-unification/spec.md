## ADDED Requirements

### Requirement: 重试装饰器增强

`with_retry` 装饰器 SHALL 支持 `retryable_exceptions` 参数，允许精确指定哪些异常可触发重试。
装饰器 SHALL 保持"只做重试，不处理降级"的原则——重试耗尽后 re-raise 最后一次异常。

#### Scenario: 带异常类型的重试

- **WHEN** `@with_retry(retryable_exceptions=(TimeoutError, ConnectionError))` 应用于函数
- **THEN** 只有 TimeoutError 和 ConnectionError 触发重试；其他异常立即 re-raise

#### Scenario: 降级由调用方处理

- **WHEN** 调用方使用 `try: result = with_retry(func)() except Exception: result = fallback`
- **THEN** 重试耗尽后抛出异常，被调用方 catch，执行降级逻辑

### Requirement: rag_chain.py 内联重试替换

`rag_chain.py` 中的 rerank 和 LLM 调用重试 SHALL 替换为 `with_retry` 装饰器。降级逻辑（rerank 失败用原始顺序、LLM 失败 yield 错误）SHALL 通过调用方的 try/except 实现。

#### Scenario: Rerank 重试失败降级

- **WHEN** reranker 调用连续失败超过 RETRY_MAX_ATTEMPTS 次
- **THEN** with_retry 抛出异常，调用方 catch，使用原始检索顺序（按 distance 排序）

#### Scenario: LLM 流式重试失败降级

- **WHEN** LLM 流式调用连续失败超过 RETRY_MAX_ATTEMPTS 次
- **THEN** with_retry 抛出异常，调用方 catch，yield 错误消息

### Requirement: chat.py 内联重试替换

`chat.py` 中的持久化重试 SHALL 替换为 `with_retry` 装饰器。当前线性退避（0.5s, 1s, 1.5s）SHALL 替换为指数退避（与 models.py 一致）。
