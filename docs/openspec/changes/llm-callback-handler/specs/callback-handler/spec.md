## ADDED Requirements

### Requirement: Langfuse CallbackHandler 可开关集成
系统 SHALL 支持通过 `LANGFUSE_ENABLE` 配置项控制 Langfuse tracing 的启用与关闭。
- SHALL 在 `LANGFUSE_ENABLE=true` 时自动创建 `LangchainCallbackHandler` 实例
- SHALL 在 `LANGFUSE_ENABLE=false` 时跳过 handler 创建，不产生任何 Langfuse 网络调用
- SHALL 在创建失败时静默降级（记 warning 日志，不抛出异常），`_langfuse_handler` 为 None
- SHALL 使用模块级 Langfuse 客户端单例，供 `@observe()` 和 `CallbackHandler` 共享

#### Scenario: LANGFUSE_ENABLE=True 时创建 handler
- **WHEN** `RAGChain` 初始化且 `LANGFUSE_ENABLE=true`
- **THEN** `_langfuse_handler` 为 `LangchainCallbackHandler` 实例
- **THEN** handler 的 Langfuse 客户端与 `@observe()` 使用同一连接

#### Scenario: LANGFUSE_ENABLE=False 时不创建 handler
- **WHEN** `RAGChain` 初始化且 `LANGFUSE_ENABLE=false`
- **THEN** `_langfuse_handler` 为 None

#### Scenario: 初始化失败时降级
- **WHEN** Langfuse 客户端初始化失败（网络问题、密钥无效等）
- **THEN** 记录 warning 日志，`_langfuse_handler` 为 None
- **THEN** RAG 流水线正常运行，不抛出异常

### Requirement: LLM 调用自动 trace 捕获
系统 SHALL 通过 CallbackHandler 自动捕获 `llm.stream()` 调用的 trace 信息。
- SHALL 在 `on_llm_start` 时记录 input messages + model 名称
- SHALL 在 `on_llm_end` 时记录 output + token usage
- SHALL 在 `on_llm_error` 时记录异常信息
- SHALL 自动将 generation 关联到当前 `@observe()` trace 上下文

#### Scenario: 正常流式调用
- **WHEN** `llm.stream(messages, config={"callbacks": [handler]})` 被调用
- **THEN** Langfuse 自动创建一条 generation，包含 input messages 和 model
- **THEN** 流结束后自动补全 output 和 token usage

#### Scenario: LLM 调用失败
- **WHEN** `llm.stream()` 抛出异常
- **THEN** Langfuse generation 记录异常信息
- **THEN** 不影响重试逻辑继续执行

### Requirement: @observe 装饰器
系统 SHALL 使用 `@observe()` 装饰器管理 trace 生命周期。
- SHALL 装饰 `RAGChain.chat_with_citations()` 作为根 trace
- SHALL 自动捕获方法入参和返回值
- SHALL 自动与 CallbackHandler 的 generation trace 关联

#### Scenario: 自动创建 trace
- **WHEN** `chat_with_citations()` 被调用
- **THEN** Langfuse 自动创建一个名为 "chat_with_citations" 的 trace
- **THEN** trace 包含 kb_id、session_id、query 作为输入

### Requirement: 移除手动 tracing 代码
系统 SHALL 移除 `stream_answer()` 中手动 `start_generation/end_generation` 打点。
- SHALL `stream_answer()` 不再接收 `tracer` 和 `trace_id` 参数
- SHALL `stream_answer()` 不再调用 `tracer.start_generation()` 和 `tracer.end_generation()`
- SHALL `stream_answer()` 不再构建 `messages_snapshot`

#### Scenario: stream_answer 签名更新
- **WHEN** `stream_answer()` 被调用
- **THEN** 第三个参数为 `handler`（可选 CallbackHandler），而非 `tracer`
- **THEN** 内部无手动 start_generation/end_generation 调用

### Requirement: 修复双重 trace
系统 SHALL 统一 trace 创建入口，消除一次请求多条 trace 的问题。
- SHALL `api/chat.py` 不再手动创建 `"chat_stream"` trace
- SHALL trace 统一由 `@observe()` 在 `chain.py` 中管理

#### Scenario: 单条 trace 覆盖整条链路
- **WHEN** 一次聊天请求完成
- **THEN** Langfuse 上只出现一条 trace，包含检索、精排、LLM 生成各阶段
