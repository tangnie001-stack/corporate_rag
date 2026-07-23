## Why

LLM 调用追踪目前是手动打点方式（`LangfuseTracer.start_generation/end_generation`），与业务代码（`stream.py`）高度耦合。存在三个问题：
1. 手动打点代码散落在流式生成逻辑中，每次 LLM 调用都要重复写 input 快照、output 收集、usage 估算三段代码
2. 日志和 tracing 是两条独立的线，日志记录也在 `stream.py` 中硬编码
3. `LANGFUSE_ENABLE` 开关定义了但未被使用，无法统一控制 tracing 开关

## What Changes

- **新增 `LangfuseCallbackHandler` 工厂**：在 `langfuse_tracing.py` 中提供 `create_langfuse_handler()`，基于 `LANGFUSE_ENABLE` 开关创建 Langfuse SDK 的 `LangchainCallbackHandler`
- **新增 `LoggingCallbackHandler`**：自定义 LangChain `BaseCallbackHandler`，将 LLM 调用信息（入参、输出、token 用量、延迟）写入 Loguru 日志
- **`RAGChain` 改用 `@observe`**：用 `langfuse.decorators.observe` 替代手动 `start_trace/end_trace`
- **`stream_answer` 改用 `callbacks` 参数**：`llm.stream(messages, config={"callbacks": [langfuse_handler, logging_handler]})`，移除手动的 `start_generation/end_generation`
- **移除 `LangfuseTracer` 类**：其所有职责（trace 生命周期、generation 记录）由 `@observe` + `CallbackHandler` 替代
- **修复双重 trace**：统一 `api/chat.py` 和 `chain.py` 的 trace 创建，消除一次请求两条 trace 的问题

## Capabilities

### New Capabilities
- `callback-handler`: Langfuse CallbackHandler 集成，通过 `LANGFUSE_ENABLE` 控制，自动捕获 LLM 调用 trace
- `llm-logging`: 自定义 LoggingCallbackHandler，将 LLM 调用信息写入 Loguru 日志

### Modified Capabilities
- （无 — 不修改现有 spec 级别的行为）

## Impact

- `src/infra/llm/langfuse_tracing.py` — 重构为全局 Langfuse 客户端单例 + 工厂函数，移除 `LangfuseTracer` 类
- `src/rag/chain.py` — 添加 `@observe`，初始化 CallbackHandler，移除 `LangfuseTracer`
- `src/rag/stream.py` — 移除手动 `start_generation/end_generation`，改用 `callbacks` 参数
- `src/api/chat.py` — 修复双重 trace（移除手动创建的 "chat_stream" trace）
- 测试文件更新：`test_langfuse.py`、`test_rag_chain_tracing.py`、`test_chat.py`
- 依赖：无新增（`langfuse` 和 `langchain-core` 已存在）
