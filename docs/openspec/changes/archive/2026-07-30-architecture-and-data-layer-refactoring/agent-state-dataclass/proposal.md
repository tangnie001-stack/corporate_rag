## Why

项目中 5 个核心数据结构使用了 TypedDict / 裸 dict 来承载结构化数据，调用方必须用 `.get("key")` 手动兜底、字符串键访问，导致字段不可控、IDE 无法跳转、默认值散落各处。统一改为 dataclass，字段默认值集中定义，调用方直接用 `.key` 访问。

## What Changes

- **AgentState / RAGQueryIntent** TypedDict → dataclass，全部字段加默认值
- **ChunkData** 已有 dataclass 但 chunker 返回 `list[dict]` → chunker 接口返回 `list[ChunkData]`
- **ChatMessage** 新增 `@dataclass`，替换 `list[dict]` 贯穿 chat/retrieval/prompt
- **TokenUsage** 新增 `@dataclass`，统一两套不一致的 token 形状，修复 `usage.get("total", 0)` 永远为 0 的 bug
- **EvalReportEntity** 方法签名从 `dict` 改为 `EvalReportEntity`

## Capabilities

### New Capabilities

- `agent-state-definition`: AgentState 的数据类定义，含 `make_initial_state` 工厂方法
- `chunk-data-model`: ChunkData 作为 chunker 的标准返回类型
- `chat-message-model`: ChatMessage 作为对话消息的标准类型
- `token-usage-model`: TokenUsage 作为 token 用量的标准类型

### Modified Capabilities

- `eval-report-model`: `insert_eval_report()` 方法签名从 `report: dict` 改为 `report: EvalReportEntity`

## Impact

- `src/agents/graph/state.py` — AgentState TypedDict → dataclass
- `src/agents/graph/nodes.py` — 全部 `state.get("key")` 替换为 `state.key`
- `src/agents/graph/workflow.py` — route_by_intent/route_by_grader 适配
- `src/services/agent_service.py` — 调用 `AgentState.make_initial_state()`
- `src/infra/chunking/strategies/base.py` — `chunk() -> list[ChunkData]`
- `src/infra/chunking/strategies/parent_child.py` — 返回 ChunkData
- `src/infra/chunking/strategies/table_preserving.py` — 返回 ChunkData
- `src/infra/chunking/strategies/qa.py` — 返回 ChunkData
- `src/services/document_service.py` — chunk 属性访问改为 `.key`
- `src/eval/chunk_scorer.py` — chunk 属性访问改为 `.key`
- `src/chat/manager.py` — 返回 `list[ChatMessage]`
- `src/rag/retrieval.py` — history 类型改为 `list[ChatMessage]`
- `src/rag/prompt.py` — history 类型改为 `list[ChatMessage]`
- `src/rag/stream.py` — TokenUsage dataclass，修复 `usage.get("total", 0)` bug
- `src/agents/graph/nodes.py` — generate_node 中 `usage.get("total", 0)` 改为 `usage.total_tokens`
- `src/services/app_service.py` — `insert_eval_report()` 接收 `EvalReportEntity`
- `src/cli/eval_ragas.py` — 构造 `EvalReportEntity` 而非 dict
- `tests/` — 对应测试适配
