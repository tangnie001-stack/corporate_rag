# 领域词汇表

> 项目规范术语。每个概念只有一个标准叫法，文档/代码/对话统一用此表词汇，避免歧义。

## 核心标识符

| 术语 | 定义 | 常见错误 |
|------|------|---------|
| `kb_id` | 知识库唯一标识，UUID 字符串；`""` 表示"搜索所有知识库" | ❌ 传 `kb_name` |
| `doc_id` | 文档唯一标识，UUID | ❌ 传 MySQL 自增 ID |
| `session_id` | 会话标识，用于关联对话历史 | ❌ 传空字符串 |
| `chunk_id` | 向量库中的分块 ID，格式 `"{doc_id}:{index}"` | — |
| `trace_id` | 请求追踪 ID，格式 `trace_<uuid>` | — |

## 响应与追踪

- **响应信封**：统一响应包装 `{"code", "message", "data"}`，仅由 `ResponseEnvelopeMiddleware` 产生；业务层只 `raise` 异常，不 `return JSONResponse`
- **SSE 事件流**：聊天流式输出的事件序列，`status → token → citation → done`

## RAG 流水线

- **chunk**：文档切分后的最小检索单元，由 `src/chunking/` 分块策略产生
- **retrieval**：检索，从 ChromaDB 按 kb 召回相关 chunk
- **rerank**：精排，对召回结果重排，产出 `contexts` 进入 LLM
- **contexts**：精排后拼入 LLM prompt 的上下文片段
- **kb**：知识库（knowledge base），文档与向量的隔离单位

## Agent 状态图（LangGraph）

- **AgentState**：节点间共享的状态对象，字段名以 `LangGraphNode.*` 常量作为 key（`src/agents/graph/state.py`）
- **LangGraphNode.\***：节点名常量，字段生产-消费矩阵的 key
- **LangGraphEvent.\*** / **LangGraphKey.\***：SSE 流解析用的事件类型 / 事件 dict key

## 对话行为

- **abstention**（拒答）：检索无达标 context 时直接返回拒答文案，不回 LLM
- **clarification**（追问）：分类器发现缺失实体时向用户发起追问（`CLARIFICATION_ENABLED` 开关控制）

## 推理思考文本

- **reasoning_content**：模型的流式思考文本（chain-of-thought）。DashScope 等第三方把思考增量放在 `delta.reasoning_content`，OpenRouter 等用 `delta.reasoning`；`ChatQwenWithReasoning` 统一累积到 `AIMessageChunk.additional_kwargs["reasoning_content"]`，供上层读取与展示

## 评估指标（RAGAS）

| 指标 | 含义 |
|------|------|
| `faithfulness` | 忠实度：答案是否忠于检索上下文 |
| `answer_relevancy` | 答案相关性：是否切题 |
| `context_precision` | 上下文精确率：召回的 chunk 有多相关 |
| `context_recall` | 上下文召回率：相关信息有多少被召回 |

## 基础设施

- **ChromaDB**：向量数据库，按 kb 分 collection
- **MinIO**：文档对象存储
- **LiteLLM**：LLM 代理，`LLM_BASE_URL` 指向（默认 `http://litellm-proxy:4000`）
- **DashScope**：通义千问系列模型的提供商（Embedding / LLM / Rerank）

## 如何更新

- 新增概念/术语（新模块、新事件类型、新指标、新配置项）且会被多处使用时，登记进对应分区。
- 同一概念已有标准叫法时，沿用本表词汇，不另造新词。
- 文档、代码、命名中的术语一律与本表一致；发现不一致时以本表为准并修正别处。
