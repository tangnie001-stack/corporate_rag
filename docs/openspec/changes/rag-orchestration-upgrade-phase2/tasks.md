## 1. 依赖安装与目录结构

- [ ] 1.1 在 `pyproject.toml` 中添加 `"langgraph==1.2.9"` 依赖并安装
- [ ] 1.2 新建 `src/agents/` 目录结构：`src/agents/__init__.py`、`src/agents/graph/__init__.py`、`src/agents/grader.py`
- [ ] 1.3 新建 `src/tools/` 目录：`src/tools/__init__.py`、`src/tools/base.py`（空 ToolBase 类）
- [ ] 1.4 创建 `src/agents/graph/state.py`：定义 AgentState TypedDict（合并 RAGState + 图控制字段）
- [ ] 1.5 确认 `pytest tests/ -v` 全部通过（依赖安装不影响现有测试）

## 2. 图节点与 agent_service

- [ ] 2.1 实现 `src/agents/graph/nodes.py`：classify + rewrite + retrieve + grader + rerank + generate + format 七个节点函数，每个含 trace_id 出入日志
- [ ] 2.2 改造 `src/rag/retrieval.py`：`classify_query()` 改为输出三级分类（simple/medium/complex），同步更新 `rewrite_query()` 的映射逻辑
- [ ] 2.3 实现 `src/agents/graph/workflow.py`：StateGraph 组装 + 三级条件边 + 编译
- [ ] 2.4 实现 `src/agents/grader.py`：RetrievalGrader 规则版（关键词覆盖度评分 + 2 次重试上限）
- [ ] 2.5 实现 `src/services/agent_service.py`：图初始化、astream_events 调用、SSE 事件转换、对话历史加载、对话持久化、异常边界
- [ ] 2.6 实现 generate_node 空 context 降级逻辑（降级到 Naive RAG 直接 LLM）
- [ ] 2.7 新增 `tests/agents/graph/test_graph.py`：节点独立测试（classify/grader）
- [ ] 2.8 确认 `pytest tests/ -v` 全部通过

## 3. API 层改造与集成测试

- [ ] 3.1 改造 `src/api/chat.py`：`_stream_rag_response` 改为调 `agent_service.stream_chat()`
- [ ] 3.2 改造 `src/services/app_service.py`：`__init__` 中挂载 `self.agent_service`（Optional + 默认创建）
- [ ] 3.3 更新 `tests/api/test_chat.py`：mock 路径从 `rag_chain.search/rerank/stream_answer` 改为 `agent_service.stream_chat`
- [ ] 3.4 新增 `tests/agents/graph/test_grader.py`：RetrievalGrader 高/低分场景测试
- [ ] 3.5 确认 `pytest tests/ -v` 全部通过

## 4. 验证与收尾

- [ ] 4.1 运行 RAGAS 评估，对比 Phase 1（chain.py 旧编排）vs Phase 2 的 faithfulness / context_precision 指标
- [ ] 4.2 验证 SSE 流式行为一致性：分段状态推送 + token �� + citation + done 事件格式与升级前一致
- [ ] 4.3 验证三级路由：手动构造 simple/medium/complex 三类查询，确认走对应路径
- [ ] 4.4 验证降级链：模拟 grader 重试用尽、rerank 结果为空、LLM 调用失败三种场景
- [ ] 4.5 清理三份文档中已废弃的 `rag/pipeline/`、`rag/state/` 引用：`docs/upgrade_plan.md`、`docs/phase2_structure.md`、`docs/financial_rag_architecture_analysis.md`
- [ ] 4.6 最终确认：`pytest tests/ -v` 全部通过 + `ruff check .` 无错误 + 无遗留 TODO/print
