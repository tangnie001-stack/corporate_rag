## Why

当前 RAG 流水线基于手动编排（`src/rag/chain.py` 的 if/else 控制流 + `src/api/chat.py` 中另一套并行编排逻辑），两份编排代码重复维护。随着条件路由、质量闭环、降级策略等需求的引入，命令式代码的复杂度将线性增长，亟需升级为 LangGraph StateGraph 统一编排。

## What Changes

- **新增** `agents/graph/` 目录：LangGraph StateGraph 定义（state.py、workflow.py、nodes.py）
- **新增** `services/agent_service.py`：图生命周期管理（初始化、执行、SSE 事件转换、降级处理）
- **新增** `agents/grader.py`：规则版 RetrievalGrader（关键词覆盖度评分）
- **新增** `src/tools/` 目录：工具抽象框架（空目录，为 Phase 3 Agent 铺路）
- **改造** `src/api/chat.py`：从直接调 `rag_chain` 改为调 `agent_service.stream_chat()`
- **改造** `src/services/app_service.py`：挂载 `agent_service` 属性
- **改造** `src/rag/retrieval.py`：`classify_query()` 返回三级分类（simple/medium/complex 替代原有 four-way）
- **新增** `tests/agents/graph/test_graph.py`：图节点与条件边测试
- **不创建** `rag/pipeline/` 目录（职责与 agents/graph/ 重复，取消计划）
- **不创建** `rag/state/` 目录（状态合并到 `agents/graph/state.py`，取消计划）
- **新增** 依赖 `langgraph==1.2.9`
- **所有节点函数必须包含 trace_id 出入日志**

**BREAKING**: `api/chat.py` 不再直接暴露 `rag_chain.search/rerank/stream_answer` 方法，SSE 路径改为统一调 `agent_service.stream_chat()`

## Capabilities

### New Capabilities
- `langgraph-orchestration`: LangGraph StateGraph 定义，含 classify/rewrite/retrieve/grader/rerank/generate/format 七个节点，支持三级路由（simple/medium/complex）
- `adaptive-routing`: 查询复杂度三级路由（simple→Naive RAG / medium→Enhanced RAG / complex→Agentic RAG），带降级链
- `retrieval-grader`: 规则版检索质量评分器，低分触发查询重写循环（2 次上限）
- `graceful-degradation`: 三级降级策略（Agent→Enhanced RAG→Naive RAG→错误消息），保证始终返回结果而非报错
- `agent-service`: 图生命周期管理服务（初始化、执行、SSE 事件映射、日志追踪）

### Modified Capabilities
- `retrieval-quality`: `classify_query()` 改为输出三级分类 simple/medium/complex

## Impact

- `src/agents/graph/` — 新增 5 个文件
- `src/rag/retrieval.py` — classify_query() 三级分类改造
- `src/services/agent_service.py` — 新增
- `src/api/chat.py` — SSE 入口改造
- `src/services/app_service.py` — 挂载 agent_service
- `pyproject.toml` — 新增 `langgraph==1.2.9`
- `tests/api/test_chat.py` — mock 路径更新
- `tests/agents/graph/test_graph.py` — 新增
- `src/rag/chain.py` — 不变（仅 RAGAS eval 用）
