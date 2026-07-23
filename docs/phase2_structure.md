# Phase 2 完成后项目结构

生成日期：2026-07-22

## 一、完整目录树

```
src/
├── main.py                         # FastAPI 入口
├── models.py                       # LLM/Embedding/Rerank 工厂
│
├── api/                            # 路由层：参数校验 → 调 service → 返回
│   ├── chat.py                     # ✎ SSE 路由入口，调 graph.astream_events()
│   ├── sse_utils.py                # SSE 格式化
│   ├── dependencies.py             # FastAPI Depends 统一管理
│   ├── auth.py                     # 认证
│   ├── documents.py                # 文档 CRUD
│   ├── knowledge_base.py           # 知识库 CRUD
│   ├── sessions.py                 # 会话管理
│   ├── kb_eval.py                  # KB 评估
│   ├── health.py                   # 健康检查
│   ├── llm_test.py                 # LLM 连通性测试
│   ├── ragas_generate.py           # RAGAS 测试集生成
│   └── model/
│       ├── request.py              # 请求模型
│       └── response.py             # 响应模型
│
├── services/                       # 业务编排层
│   ├── app_service.py              # 顶层编排：持有 kb/doc + RAGChain
│   ├── agent_service.py            # ★ 新增：图生命周期管理（初始化/执行/中断/持久化）
│   ├── kb_service.py               # 知识库 CRUD
│   └── document_service.py         # 文档上传/解析/分块/入库
│
├── rag/                            # RAG 核心能力
│   ├── state/                      # ★ 新增：Pipeline 状态层
│   │   └── rag_state.py            # RAGState TypedDict
│   ├── pipeline/                   # ★ 新增：流水线编排层
│   │   ├── orchestrator.py         # 编排器：决定各阶段执行顺序
│   │   └── stages.py               # 预检索/检索/后检索阶段定义
│   ├── chain.py                    # ○ 不变（仅 RAGAS eval 用，不走 graph）
│   ├── retrieval.py                # ○ 不变：search() / rerank_results() / rewrite_query()
│   ├── stream.py                   # ○ 不变：stream_answer()
│   ├── prompt.py                   # ○ 不变：build_prompt() / build_simple_prompt()
│   └── context.py                  # ○ 不变：RAGContext dataclass
│
├── agents/                         # ★ 新增：LangGraph 图定义
│   ├── graph/
│   │   ├── state.py                # AgentState（基于 RAGState 扩展，含图运行态字段）
│   │   ├── workflow.py             # StateGraph 组装（节点注册 + 边连接 + 编译）
│   │   └── nodes.py                # 各节点函数（含 trace_id 出入日志）：
│   │                                #   classify() → QueryRouter
│   │                                #   rewrite()  → rewrite_query()
│   │                                #   retrieve() → search()
│   │                                #   grader()   → RetrievalGrader
│   │                                #   rerank()   → rerank_results()
│   │                                #   generate() → stream_answer()
│   │                                #   format()   → 构建输出 + citations
│   └── grader.py                   # ★ 新增：RetrievalGrader（规则版）
│
├── tools/                          # ★ 新增：工具集框架（空壳，Phase 3 填充）
│   └── base.py                     # ToolBase 抽象基类（预留）
│
├── chat/                           # ○ 不变：对话管理
│   ├── manager.py                  # ChatManager（Redis + InMemory）
│   └── persistence.py              # MySQL 持久化
│
├── config/                         # ○ 不变：配置
│   ├── settings.py
│   ├── prompts.py
│   ├── queries.py
│   └── response_codes.py
│
├── infra/                          # ○ 不变：基础设施
│   ├── db/
│   │   ├── vector_store.py         # ChromaDB
│   │   ├── mysql_db.py             # MySQL
│   │   └── file_store.py           # MinIO
│   ├── search/
│   │   ├── bm25_index.py           # BM25 + RRF 融合
│   │   └── query_router.py         # QueryRouter
│   ├── llm/
│   │   ├── langfuse_tracing.py     # Langfuse 追踪
│   │   └── prompt_manager.py       # Prompt 管理
│   ├── chunking/                   # 分块策略
│   ├── redis_client.py
│   └── errors.py
│
├── middleware/                      # ○ 不变
├── core/                            # ○ 不变
├── parsers/                         # ○ 不变
├── cli/                             # ○ 不变
└── eval/                            # ○ 不变

tests/                               # ✎ 新增子目录
├── rag/
│   ├── test_state.py               # ★ 新增
│   ├── test_pipeline.py            # ★ 新增
│   └── ...                         # 现有测试文件
├── agents/
│   └── graph/
│       └── test_graph.py           # ★ 新增：节点/边/条件路由测试
│       └── test_grader.py          # ★ 新增：RetrievalGrader 测试
├── api/                             # ○ 不变
├── services/                        # ○ 不变
└── ...
```

**图例**：○ 不变 · ✎ 改造 · ★ 新增 · 已删除的不再列出

---

## 二、层级调用规则

```
                    ┌────── HTTP 请求 ──────┐
                    │                        │
                    ▼                        ▼
              ┌──────────┐
              │ SSE path │
              │api/chat.py│
              └─────┬────┘
                    │
                    │ 调 agent_service
                    ▼
              ┌──────────────────────────────┐
              │  services/agent_service.py   │
              │  图生命周期管理                │
              └────────┬─────────────────────┘
                       │
                       ▼
                    ▼
              ┌──────────────────────────────┐
              │  agents/graph/               │
              │  LangGraph StateGraph        │
              │  workflow.py                 │
              └────────┬─────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
      ┌────────────┐ ┌────────────┐ ┌────────────┐
      │ rag/       │ │ rag/       │ │ rag/       │
      │ retrieval  │ │ pipeline/  │ │ stream/    │
      │ .py        │ │ stages.py  │ │ prompt/    │
      └─────┬──────┘ └────────────┘ └────────────┘
            │
            ▼
      ┌────────────┐
      │ infra/     │
      │ vector_    │
      │ store /    │
      │ bm25_index │
      └────────────┘
```

### 调用规则（保持不变 + 新增）

| 调用方向 | 规则 |
| `api/` **↛** `infra/` / `rag/` | ✅ 已有，保持 |
| `api/` → `services/` | ✅ 已有，保持（非 chat 路径） |
| `api/chat.py` → `services/agent_service.py` → `agents/graph/` | ✅ SSE 路径不越层 |
| `services/` → `rag/` / `agents/` / `infra/` / `chat/` | ✅ 已有，保持 |
| `api/chat.py` → `agents/graph/` (astream_events) | ✅ SSE 路径直接调图 |
| `services/agent_service.py` → `rag/` / `agents/` / `infra/` / `chat/` | ✅ agent_service 统一调用 |
| `agents/graph/nodes.py` → `rag/retrieval.py` / `rag/pipeline/stages.py` | ✅ 节点函数调现有检索/流水线能力 |

### 关键变化：两个入口统一到一个图

```
之前（Phase 1）                   之后（Phase 2）
SSE path:                          SSE path（唯一主路径）:
api/chat.py                        api/chat.py
  ├─ search()  ───┐                  └─ services/agent_service.py
  ├─ rerank()    │  重复编排               │
  ├─ stream()   │                    ┌──────┴──────┐
  └─ persist() ───┘                 │  classify    │
                                    │  rewrite     │
Sync path (无调用方):                │  retrieve    │
chat_service.py → chain.py          │  grader      │
  ├─ route → if/else                │  rerank      │
  ├─ rewrite_if_needed              │  generate    │
  ├─ search()                       │  format      │
  ├─ rerank()                       └──────┬──────┘
  └─ stream_answer()                       │
                                     ┌──────┴──────┘
                                     │  persist     │
                                     │  (callback)  │
                                     └─────────────┘
                                    chain.py / chat_service.py:
                                    ○ 不变，仅 RAGAS eval 用
```

---

## 三、完整链路数据流（SSE 路径为例）

```
用户请求: GET /api/chat/stream?session_id=X&kb_id=Y&query=Z
  │
  ▼
┌────────────────────────────────────────────────────────┐
│ api/chat.py: chat_stream()                              │
│  1. 参数校验（FastAPI 自动）                             │
│  2. 调 _stream_rag_response() → graph.astream_events()  │
└────────────────────────────────────────────────────────┘
  │
  ▼
┌────────────────────────────────────────────────────────┐
│ agents/graph/workflow.py: 编译后的 StateGraph           │
│                                                        │
│  State: AgentState(session_id, kb_id, query, ...)      │
│                                                        │
│  ┌──── Node 1: classify ──────────────────────────┐    │
│  │ 调 QueryRouter.route(query)                      │    │
│  │ → intent: "simple" | "vague" | "complex"         │    │
│  │ → SSE: sse_status("classifying")                  │    │
│  └──────────────────────┬──────────────────────────┘    │
│                         │                               │
│          ┌──────────────┴──────────────┐                │
│          │ intent == "simple"          │ else            │
│          ▼                             ▼                │
│  ┌───────────────┐          ┌──────────────────┐        │
│  │ → skip        │          │ Node 2: rewrite  │        │
│  │   retrieval   │          │ rewrite_query()   │        │
│  │   & rerank    │          │ condense / expand  │        │
│  └───────┬───────┘          │ / decompose        │        │
│          │                  └────────┬─────────┘        │
│          │                           │                  │
│          │                  ┌────────▼─────────┐        │
│          │                  │ Node 3: retrieve │        │
│          │                  │ search().hybrid() │        │
│          │                  │ ChromaDB + BM25   │        │
│          │                  │ SSE: sse_status(  │        │
│          │                  │   "retrieving")   │        │
│          │                  └────────┬─────────┘        │
│          │                           │                  │
│          │                  ┌────────▼─────────┐        │
│          │                  │ Node 4: grader   │        │
│          │                  │ RetrievalGrader  │        │
│          │                  │ 规则版评分 0~1    │        │
│          │                  └────────┬─────────┘        │
│          │                           │                  │
│          │               score < 0.5 │  score ≥ 0.5     │
│          │                 + retry<2  │                  │
│          │                           │                  │
│          │                  ┌────────▼─────────┐        │
│          │                  │ → rewrite (loop) │        │
│          │                  │                  │        │
│          │                  └──────────────────┘        │
│          │                           │                  │
│          │                  ┌────────▼─────────┐        │
│          │                  │ Node 5: rerank   │        │
│          │                  │ rerank_results() │        │
│          │                  │ DashScopeRerank  │        │
│          │                  │ SSE: sse_status( │        │
│          │                  │   "reranking")   │        │
│          │                  └────────┬─────────┘        │
│          │                           │                  │
│          └──────────────┬────────────┘                  │
│                         ▼                               │
│  ┌──────────────────────────────────────────┐           │
│  │ Node 6: generate                          │           │
│  │ stream_answer(prompt, llm)                │           │
│  │ SSE: sse_token(token) × N                │           │
│  └────────────────────┬─────────────────────┘           │
│                       │                                 │
│  ┌────────────────────▼─────────────────────┐           │
│  │ Node 7: format                            │           │
│  │ 构建 citations / 去重 / query-biased       │           │
│  │ 高亮 snippet                               │           │
│  │ SSE: sse_citation() × M                  │           │
│  └────────────────────┬─────────────────────┘           │
│                       │                                 │
└───────────────────────┼─────────────────────────────────┘
                        │
  ┌─────────────────────▼─────────────────────┐
  │ 持久化（api/chat.py 内联）                   │
  │ 保存 session + messages 到 MySQL           │
  │ SSE: sse_done()                            │
  └───────────────────────────────────────────┘
```

---

## 四、各目录职责总结

### rag/ 目录（核心能力，保留不变）

| 子模块 | 职责 | 使用方 |
|:---|:---|:---|
| `state/rag_state.py` | RAGState 类型定义 | `agents/graph/state.py` 引用 |
| `pipeline/orchestrator.py` | 编排逻辑：决定各阶段执行顺序 | `agents/graph/nodes.py` 调用 |
| `pipeline/stages.py` | 预检索/检索/后检索阶段定义 | `agents/graph/nodes.py` 调用 |
| `retrieval.py` | `search()` / `rerank_results()` / `rewrite_query()` | `agents/graph/nodes.py` 调用 |
| `stream.py` | `stream_answer()` — LLM 流式生成 | `agents/graph/nodes.py` 调用 |
| `prompt.py` | `build_prompt()` / `build_simple_prompt()` | `agents/graph/nodes.py` 调用 |
| `context.py` | `RAGContext` dataclass | `retrieval.py` / 各节点返回值类型 |
| `chain.py` | **不变**：仅 RAGAS eval 用，不在主链路中 | `services/app_service.py`（仅 eval 时） |

### agents/ 目录（新增，Phase 3 Agent 的起点）

| 子模块 | 职责 | 使用方 |
|:---|:---|:---|
| `graph/workflow.py` | StateGraph 组装：注册节点、连接边、编译 | `api/chat.py` SSE 入口初始化时调用 |
| `graph/state.py` | AgentState：基于 RAGState 扩展图运行态字段 | `workflow.py` 图状态类型 |
| `graph/nodes.py` | 各节点函数：`classify`/`rewrite`/`retrieve`/`grader`/`rerank`/`generate`/`format` | `workflow.py` 注册时引用 |
| `grader.py` | RetrievalGrader 规则版 | `nodes.py` 中 grader 节点调用 |

### tools/ 目录（新增，Phase 3 填充）

| 子模块 | 职责 |
|:---|:---|
| `base.py` | ToolBase 抽象基类 | 

---

## 五、当前 → Phase 2 变更摘要

| 文件 | 操作 | 理由 |
|:---|:---|:---|
| `services/agent_service.py` | ★ 新建 | 图生命周期管理（初始化/执行/中断/持久化） |
| `rag/state/rag_state.py` | ★ 新建 | Pipeline 状态统一管理 |
| `rag/pipeline/orchestrator.py` | ★ 新建 | 编排逻辑从 chain.py 抽出 |
| `rag/pipeline/stages.py` | ★ 新建 | 各阶段定义 |
| `agents/graph/workflow.py` | ★ 新建 | StateGraph 定义 |
| `agents/graph/state.py` | ★ 新建 | AgentState |
| `agents/graph/nodes.py` | ★ 新建 | 7 个节点函数 |
| `agents/grader.py` | ★ 新建 | RetrievalGrader |
| `tools/base.py` | ★ 新建 | 工具基类（框架） |
| `rag/chain.py` | ○ 不变 | 仅 RAGAS eval 用，不参与主链路 |
| `api/chat.py` | ✎ 改造 | `_stream_rag_response` 改为 `graph.astream_events()` |
chat_service.py` | ✎ 改造 | 改为 agent_service 的轻量包装，或删除 |/chat_service.py` | ○ 不变 | 当前无调用方，Phase 3 再考虑 |
| `tests/rag/test_state.py` | ★ 新建 | RAGState 测试 |
| `tests/rag/test_pipeline.py` | ★ 新建 | 编排器测试 |
| `tests/agents/graph/test_graph.py` | ★ 新建 | 节点/边/路由测试 |
| `tests/agents/graph/test_grader.py` | ★ 新建 | Grader 测试 |
| `rag/retrieval.py` | ○ 不变 | 保持 |
| `rag/stream.py` | ○ 不变 | 保持 |
| `rag/prompt.py` | ○ 不变 | 保持 |
| `rag/context.py` | ○ 不变 | 保持 |
