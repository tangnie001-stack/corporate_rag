## 1. SSE 流式路径日志（api/chat.py）

- [ ] 1.1 在 `_stream_rag_response` try 块开头添加入口日志 `"Chat stream start"`（query/session_id/kb_id）
- [ ] 1.2 在检索/重排序/生成/完成四点添加 `time.perf_counter()` 计时（t0~t3）
- [ ] 1.3 在 `yield sse_done()` 前添加完成日志 `"Chat stream completed"`（各阶段耗时 + token 用量 + citations 数）
- [ ] 1.4 在 `_persist_conversation` 成功后添加确认日志 `"Conversation persisted"`（session_id/kb_id/sources）
- [ ] 1.5 运行 `pytest tests/api/test_chat.py -v` 确认测试通过
- [ ] 1.6 Commit：`git commit -m "feat: add SSE stream lifecycle and timing logs (api/chat.py)"`

## 2. 同步路径日志（rag/chain.py）

- [ ] 2.1 在 `chat_with_citations` 入口添加入口日志（route/query_len/original_query）
- [ ] 2.2 在 `_rewrite_if_needed` 调用后添加重写日志（原查询 → 改写后查询）
- [ ] 2.3 在检索/重排序/生成/完成四点添加 `time.perf_counter()` 计时（t0~t3）
- [ ] 2.4 在 return 前添加完成日志（各阶段耗时 + 检索结果数 + 重排序上下文数 + token 用量）
- [ ] 2.5 运行 `pytest tests/ -v` 确认测试通过
- [ ] 2.6 Commit：`git commit -m "feat: add sync path lifecycle and timing logs (rag/chain.py)"`

## 3. 检索和重排序日志（rag/retrieval.py）

- [ ] 3.1 在 hybrid 分支的日志消息中追加 `mode=hybrid` 字段
- [ ] 3.2 在默认分支的日志消息中追加 `mode=dense` 字段
- [ ] 3.3 在 `rerank_results` 正常路径的 `return contexts`（第 100 行）前添加日志（输入结果数 → 输出上下文数, top_score）
- [ ] 3.4 运行 `pytest tests/ -v` 确认测试通过
- [ ] 3.5 Commit：`git commit -m "feat: add search mode and rerank completion logs (rag/retrieval.py)"`

## 4. 生成完成日志（rag/stream.py）

- [ ] 4.1 在 `stream_answer` 的 return 前（第 76 行）添加生成完成日志（耗时 ms / 字符数 / token 用量）
- [ ] 4.2 在退出前确保 timer 变量在作用域内可用
- [ ] 4.3 运行 `pytest tests/ -v` 确认测试通过
- [ ] 4.4 Commit：`git commit -m "feat: add generation completion logs with token usage (rag/stream.py)"`

## 5. 格式化与最终验证

- [ ] 5.1 运行 `ruff format src/api/chat.py src/rag/` 和 `ruff check --fix src/api/chat.py src/rag/`
- [ ] 5.2 运行完整测试套件 `pytest tests/ -v`，确认所有通过
- [ ] 5.3 如有 lint 修复则提交：`git commit -m "style: format and lint RAG pipeline logging changes"`
