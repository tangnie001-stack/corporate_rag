## 1. LoggingCallbackHandler 实现

- [ ] 1.1 新建 `src/infra/llm/logging_handler.py`，实现 `LoggingCallbackHandler(BaseCallbackHandler)`，覆盖 `on_llm_start` / `on_chunk` / `on_llm_end` / `on_llm_error`
- [ ] 1.2 在 `__init__` 中记录首 token 到达时间，`on_llm_end` 输出首 token 延迟 + 总耗时 + token 用量

## 2. langfuse_tracing.py 重构

- [ ] 2.1 新增模块级 `_ensure_langfuse()` 函数，读取 `LANGFUSE_ENABLE` 开关，创建全局 Langfuse 客户端单例
- [ ] 2.2 新增 `create_langfuse_handler()` 工厂函数，返回 `LangchainCallbackHandler` 或 None
- [ ] 2.3 **保留** `LangfuseTracer` 类（暂时不动，后续步骤引用跑通后再移除）

## 3. RAGChain 改造

- [ ] 3.1 `rag/chain.py`: 在 `__init__` 中调用 `create_langfuse_handler()` 赋值给 `self._langfuse_handler`
- [ ] 3.2 `rag/chain.py`: 创建 `LoggingCallbackHandler` 实例赋值给 `self._logging_handler`
- [ ] 3.3 `rag/chain.py`: 给 `chat_with_citations()` 添加 `@observe(name="chat_with_citations")`
- [ ] 3.4 `rag/chain.py`: `stream_answer()` 方法中传递 `self._langfuse_handler` + `self._logging_handler` 给 `rag/stream.py` 的 `stream_answer()`

## 4. stream.py 简化

- [ ] 4.1 修改 `stream_answer()` 签名，`tracer` 参数改为 `handler`，移除 `trace_id` ��数
- [ ] 4.2 移除 `messages_snapshot` 构建、`tracer.start_generation()`、`tracer.end_generation()`、`estimate_usage()` 调用
- [ ] 4.3 构建 `config={"callbacks": [h for h in [langfuse_handler, logging_handler] if h]}` 传入 `llm.stream()`
- [ ] 4.4 移除手动 `logger.info("Generation completed...")` 和 `logger.info("RAG first_token_latency...")`（这些由 LoggingCallbackHandler 接管）
- [ ] 4.5 `stream_answer()` 调用方（`chain.py`）更新传参

## 5. API 层修复

- [ ] 5.1 `api/chat.py`: 移除 `_stream_rag_response()` 中手动创建 `"chat_stream"` trace 的代码（第 170-176 行）
- [ ] 5.2 `api/chat.py`: 移除对 `svc.rag_chain._tracer` 的引用
- [ ] 5.3 `api/chat.py`: 确认 trace_id 不再用于 SSE 阶段推送（status/citation 等不依赖 trace_id）

## 6. 清理旧代码

- [ ] 6.1 确认所有引用 `LangfuseTracer` 的地方已替换
- [ ] 6.2 从 `langfuse_tracing.py` 中移除 `LangfuseTracer` 类
- [ ] 6.3 `rag/chain.py` 移除 `from src.infra.llm.langfuse_tracing import LangfuseTracer`

## 7. 测试更新

- [ ] 7.1 更新 `tests/infra/llm/test_langfuse.py`：改为测试 `create_langfuse_handler()` 工厂函数
- [ ] 7.2 更新 `tests/rag/test_rag_chain_tracing.py`：对齐新的 `_stream_answer` 签名（callbacks 用 config 方式传入）
- [ ] 7.3 更新 `tests/api/test_chat.py`：确认 trace_id 相关 mock 不再需要

## 8. 验证

- [ ] 8.1 `ruff format . && ruff check . --fix` 无错误
- [ ] 8.2 `pytest tests/ -v` 全部通过
- [ ] 8.3 手动触发 /chat/stream 端点，检查日志输出含 LLM 调用信息
- [ ] 8.4 `LANGFUSE_ENABLE=false` 时日志正常记录、无 Langfuse 调用
