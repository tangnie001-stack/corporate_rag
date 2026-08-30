## Context

当前 corporate_rag 的 RAG 问答流水线有两套并行编排：

1. **SSE 路径**（`api/chat.py` 的 `_stream_rag_response`）：手动编排 search → rerank → stream_answer → citations → persist 四个阶段
2. **同步路径**（`rag/chain.py` 的 `chat_with_citations`）：route → rewrite → search → rerank → stream_answer

两者调用相同的 `rag/retrieval.py` / `rag/stream.py` / `rag/prompt.py`，但编排逻辑分散在两处。随着需求增加（条件路由、质量评估、降级策略），两套编排的维护成本会持续上升。

设计方案参考：`docs/upgrade_plan.md`、`docs/phase2_structure.md`、`docs/financial_rag_architecture_analysis.md`

## Goals / Non-Goals

**Goals:**
- 用 LangGraph StateGraph 统一 RAG 问答编排，消除代码重复
- 引入三级路由（simple / medium / complex），按查询复杂度分配不同管道
- 引入 RetrievalGrader 规则版质量评估，低质量检索自动重试
- 引入三级降级策略，保证复杂路径失败时平滑降级
- 所有节点函数包含 trace_id 出入日志
- 为 Phase 3（Reflection / Human-in-loop）打好图结构基础

**Non-Goals:**
- 不改变 RAG 核心能力（混合检索 ChromaDB+BM25+RRF、重排序 DashScopeRerank、查询改写 expand/condense/decompose）——这些在 `rag/` 下保持不变
- 不改 `rag/chain.py`——仅 RAGAS eval 用，Phase 2 不动
- 不改 `chat_service.py`——当前无调用方，待 Phase 3 决定去留
- 不引入 Checkpointer（MemorySaver）——Phase 3 Human-in-loop 时再加
- 不做 LLM 版 RetrievalGrader / FaithfulnessChecker / Reflection——Phase 3 范围

## Decisions

### D1. 图 vs 命令式编排
**选择**：LangGraph StateGraph
**理由**：条件边天然解决"不同查询走不同路径"的需求，无需手动 if/else。LangGraph 1.2.x 已生产稳定，且当前项目环境已安装。

### D2. 三级路由（simple / medium / complex）
**选择**：三级路由替代原有的两级（simple / complex）
**理由**：调研数据显示中等难度查询（2-3 个事实关联）Agent 收益仅 +4%，不值得走 grader 循环。三级路由让 medium 跳过 grader 直接 rerank → generate，节省成本和延迟。
- simple → generate（Naive RAG，无检索）
- medium → rewrite → retrieve → rerank → generate（Enhanced RAG，跳过 grader）
- complex → rewrite → retrieve → grader ↔ rewrite（重试）→ rerank → generate（Agentic RAG）

### D3. 降级策略（三层）
**选择**：Agent 失败 → Enhanced RAG → Naive RAG → 错误消息
**理由**：保证复杂路径在 grader 循环耗尽、rerank 结果为空、LLM 调用失败等场景下都能返回结果而非报错。每个降级点向前一阶段退化，保持体验平滑。

### D4. 状态定义位置
**选择**：合并到 `agents/graph/state.py`，去掉 `rag/state/`
**理由**：RAGState 只被 agents/graph/ 使用，rag/ 下的核心函数（retrieval.py/stream.py/prompt.py）都接收独立参数，不依赖 RAGState。放在 agents/ 下更自然。

### D5. SSE 转换位置
**选择**：放 `services/agent_service.py` 内部
**理由**：SSE 事件与图的事件流（astream_events）强相关——`on_chain_start` 对应 `sse_status`、`on_chat_model_stream` 对应 `sse_token`。放在 agent_service 里保持 api 层纯净，只做 HTTP 转发。

### D6. agent_service 注入方式
**选择**：`AppService.__init__` 走 `Optional[AgentService] = None`，内部默认创建。AgentService 持有 `vector_store`、`bm25`、`llm`、`reranker`、`prompt_manager`、`chat_manager` 六个依赖。
**理由**：和项目现有模式一致（参看 `AppService.__init__` 中其他参数的注入方式）。`chat_manager` 用于在 `stream_chat` 开始时加载对话历史、结束后持久化。

### D7. 日志要求
**选择**：每个节点函数包含 `[trace_id] nodename action: detail` 格式的出入日志
**理由**：LangGraph 图执行时错误堆栈只停在 `graph.ainvoke()`，无法直接定位到出错的节点。trace_id 日志是追踪节点执行链的主要手段。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|:---|:---|
| **调试困难**：LangGraph 节点错误堆栈不直接指到问题函数 | 每个节点加 trace_id 出入日志；支持 LangSmith 可观测性接入 |
| **simple/medium/complex 分级不准**：QueryRouter 当前基于规则（Regex），可能误分类 | 先用规则版；Phase 3 可升级为 LLM 分类器 |
| **RetrievalGrader 误判**：规则版基于关键词覆盖度，不适用于抽象概念查询 | 设置 2 次重试上限；误判时通过降级链兜底 |
| **引入新依赖**：langgraph 1.2.9 新增 ~5 个子包 | 已验证与当前 langchain-core 1.4.8 / langchain 1.3.11 完全兼容 |

## Migration Plan

分 4 个 PR 逐步推进，每个 PR 独立可测：

1. **PR 1：依赖 + 目录结构** — 安装 langgraph、新建 `src/agents/graph/` 和 `src/tools/` 目录（空文件）、新建 `src/agents/grader.py`（空类）
2. **PR 2：图节点 + agent_service** — 实现七个节点函数、AgentState、StateGraph 组装、RetrievalGrader 规则版、agent_service
3. **PR 3：api 改造 + 测试** — api/chat.py 调 agent_service、app_service 挂载 agent_service、测试更新
4. **PR 4：验证与清理** — 用 RAGAS 跑评估对比 Phase 1 vs Phase 2、清理 docs/ 中旧引用

**回滚策略**：PR 1-2 不改变任何行为，可随时回滚。PR 3 可保留旧的 SSE 路径作为备选入口，通过配置开关切换。

## Open Questions

- QueryRouter 的三级分类阈值：simple / medium / complex 的具体判定标准需要在实现过程中调优

## Memory Decision

对话历史管理方案见 `memory-research.md`。

**结论**：Phase 2 保持现有 ChatManager（Redis + 滑动窗口 + MySQL 持久化），
不引入 LangGraph Store / Checkpointer 等 L2/L3 记忆机制。
agent_service 通过 chat_manager 加载/保存历史即可。
- PR 3 是否需要保留旧 SSE 路径作为灰度开关？待实现时按风险决定
