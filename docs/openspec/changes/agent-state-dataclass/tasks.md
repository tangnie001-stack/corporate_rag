## 1. AgentState — 核心数据类改造

- [ ] 1.1 将 RAGQueryIntent 从 TypedDict 改为 dataclass，route 默认 ""，rewritten 默认 False
- [ ] 1.2 将 AgentState 从 TypedDict 改为 dataclass，所有字段加默认值
- [ ] 1.3 新增 `@classmethod make_initial_state()` 工厂方法
- [ ] 1.4 更新 agent_service.py 调用 `AgentState.make_initial_state()`
- [ ] 1.5 更新 eval_ragas.py 使用 `AgentState.make_initial_state()`
- [ ] 1.6 更新 nodes.py `_tid()` — `state.get("trace_id")` → `state.trace_id`
- [ ] 1.7 更新 nodes.py classify/rewrite 节点 — `state.get("query")` → `state.query`，返回 `RAGQueryIntent(...)`
- [ ] 1.8 更新 nodes.py retrieve/grader/rerank 节点 — `state.get("rewritten_query")` / `state.get("kb_id")` / `state.get("retrieval_results")` → 属性访问
- [ ] 1.9 更新 nodes.py generate/format 节点 — `state.get("contexts")` / `state.get("_history")` / `state.get("intent")` / `state.get("downgraded")` → 属性访问
- [ ] 1.10 更新 workflow.py — route_by_intent/route_by_grader 适配
- [ ] 1.11 更新 test_state.py — 4 个测试全改为 dataclass 构造 + 属性访问
- [ ] 1.12 ruff check + pytest tests/agents/graph/ 通过

## 2. ChunkData — Chunker 返回类型标准化

- [ ] 2.1 base.py 接口 `chunk() -> list[ChunkData]`
- [ ] 2.2 parent_child.py 返回 `ChunkData` 而非 dict
- [ ] 2.3 table_preserving.py 返回 `ChunkData` 而非 dict
- [ ] 2.4 qa.py 返回 `ChunkData` 而非 dict
- [ ] 2.5 document_service.py _merge_tiny_chunks / enrich_chunk_pages — `c["content"]` → `c.content`，`c["metadata"]` → `c.metadata`
- [ ] 2.6 document_service.py ChunkData 包装代码（line 391-393 处）移除，chunker 直接返回 ChunkData
- [ ] 2.7 eval/chunk_scorer.py `chunk["content"]` → `chunk.content`
- [ ] 2.8 ruff check + pytest tests/ 通过

## 3. ChatMessage — 对话消息类型化

- [ ] 3.1 新增 `@dataclass ChatMessage: role: str, content: str`
- [ ] 3.2 chat/manager.py `add_message_async()` 构造 `ChatMessage` 对象
- [ ] 3.3 chat/manager.py `get_history_async()` 返回 `list[ChatMessage]`（Redis 反序列化后转换）
- [ ] 3.4 chat/manager.py in-memory 存储改为 `list[ChatMessage]`
- [ ] 3.5 rag/retrieval.py `expand_query()`, `rewrite_query()` — `msg["role"]` / `msg["content"]` → `msg.role` / `msg.content`，签名改为 `list[ChatMessage]`
- [ ] 3.6 rag/prompt.py `build_prompt()`, `build_simple_prompt()` — `msg["role"]` / `msg["content"]` → `msg.role` / `msg.content`，签名改为 `list[ChatMessage]`
- [ ] 3.7 ruff check + pytest tests/ 通过

## 4. TokenUsage — Token 用量类型化 + bug 修复

- [ ] 4.1 新增 `@dataclass TokenUsage: prompt_tokens, completion_tokens, total_tokens`
- [ ] 4.2 rag/stream.py `estimate_usage()` 返回 `TokenUsage` 而非 `dict`
- [ ] 4.3 rag/stream.py `stream_answer()` 使用 `TokenUsage` 统一两套形状
- [ ] 4.4 agents/graph/nodes.py `generate_node` 中 `usage.get("total", 0)` → `usage.total_tokens`
- [ ] 4.5 ruff check + pytest tests/ 通过

## 5. EvalReport — 方法签名类型化

- [ ] 5.1 services/app_service.py `insert_eval_report(report: EvalReportEntity)` 替代 `report: dict`
- [ ] 5.2 cli/eval_ragas.py 构造 `EvalReportEntity` 而非 dict
- [ ] 5.3 ruff check + pytest tests/ 通过

## 6. 集成验证

- [ ] 6.1 `ruff check .` 全量通过
- [ ] 6.2 `pytest tests/ -v` 全量通过
- [ ] 6.3 代码审查：无残留 `c["content"]`、`msg["role"]`、`state.get(` 等 dict 访问模式
