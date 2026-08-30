# Proposal: RAG 引用来源落地校验与低相关度门控

## Why

排查 trace `trace_50eaa1bb-00d9-4422-a8e2-3c1c137fee33` 暴露了三个问题：

1. **回答内容丢失（Bug）**：`agent_service.stream_chat` 按事件 `name` 判断节点来源，但 `on_chat_model_stream` 事件的 `name` 是模型类名（`ChatOpenAI`）而非节点名（`generate`），导致 generate 节点所有 token 被过滤，前端收不到任何回答文本。
2. **引用来源失真**：`format_node` 把所有精排 context 去重后全部列为引用，不做"回答是否真的引用了该来源"的落地校验，低相关或未被引用的来源也被展示。
3. **低相关度无门控**：rerank 后无分数阈值，0.18 分的 context 也送入 LLM；重试耗尽后"强行生成"，没有明确的"未找到相关数据"出口，且引用照列。

业界共识（Perplexity/RAGFlow/Dify/Claude Citations 及 self-RAG/CRAG 等）：**引用只展示被回答实际引用的来源**；**低相关时应拒绝回答而非硬塞不相关段落**（abstention paradox：不相关上下文反而加剧幻觉）。

## What Changes

- **修复 SSE 回答丢失 Bug**：`agent_service.py` 的 `CHAT_MODEL_STREAM` 过滤条件改为匹配 `event["metadata"]["langgraph_node"]`，让 generate 节点 token 正常流到前端。
- **引用落地校验**：`generate_node` 输出的回答中要求 LLM 标注 `[n]` 引用标记（prompt 约束）；`format_node` 只保留回答中实际引用的来源，未引用的剔除；回答含拒答语（"未在文档中找到相关数据"）时不输出任何引用。
- **Rerank 分数阈值**：`rerank_results` 增加可配置的最低分数阈值（默认 0.3），低于阈值的 context 不送入 LLM。
- **低相关 abstention 出口**：contexts 为空或全部低于阈值时，走专用 abstention 分支——回答明确"未在文档中找到相关数据"，不列出引用，不强行带低分 context 生成。
- **降级路径整合**：重试耗尽（`downgraded=true`）且 rerank 后仍无达标 context 时，同样走 abstention 分支而非 Naive RAG。

## Capabilities

### New Capabilities
- `answer-grounding` — 回答引用落地校验与引用展示规则
- `relevance-gating` — 检索结果相关度门控与 abstention 出口

### Modified Capabilities
- 无

## Impact

- `src/services/agent_service.py` — 事件过滤逻辑、引用事件输出、abstention 事件流
- `src/agents/graph/nodes.py` — `format_node` 引用过滤、`generate_node` abstention 分支
- `src/agents/graph/workflow.py` — grader 降级路径路由到 abstention
- `src/rag/retrieval.py` — `rerank_results` 分数阈值
- `src/rag/prompt.py` / `src/config/prompts.py` — 引用标记指令
- `src/config/settings.py` — 新增 `RERANK_MIN_SCORE` 配置项
- `src/utils/sse.py` — 可能需要新增 abstention 事件（或复用 token/error）
- `tests/services/test_agent_service.py` — 补充 token 流与引用过滤测试
- 前端：引用渲染逻辑需容忍"无引用"场景（若前端按引用数量渲染来源栏）
