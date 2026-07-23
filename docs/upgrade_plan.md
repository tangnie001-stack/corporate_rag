# RAG 系统升级规划：Phase 2 → Phase 3

生成日期：2026-07-22

## 背景

当前 corporate_rag 的 RAG 流水线基于命令式编排（`src/rag/chain.py` + `src/api/chat.py` 中两套并行编排逻辑）。升级目标是用 LangGraph StateGraph 统一编排，逐步引入条件路由、质量闭环和 Agent 能力。

参考：`docs/financial_rag_architecture_analysis.md`

---

## 一、总览

```
当前状态                                  目标状态
┌─────────────────┐                 ┌──────────────────────────┐
│ rag/chain.py     │    Phase 2     │ LangGraph StateGraph     │
│ (if/else 编排)   │ ────────────→  │ · 4-6 节点线性图         │
│ api/chat.py      │                │ · 条件路由(simple跳过检索)│
│ (重复编排逻辑)    │                │ · RetrievalGrader(规则版) │
│ 无质量评估        │                │ · SSE path + sync path 统一│
│ 无状态管理        │                │                          │
└─────────────────┘                 └──────────┬───────────────┘
                                               │
                                    Phase 3    │
                                               ▼
                                    ┌──────────────────────────┐
                                    │ 完整 Agentic RAG          │
                                    │ · FaithfulnessChecker     │
                                    │ · Reflection              │
                                    │ · regenerate_aggregator   │
                                    │ · Human-in-loop           │
                                    └──────────────────────────┘
```

---

## 二、Phase 2 详细规划

### 2.1 总体范围

| 里程碑 | 内容 | 风险 | 收益 |
|:---|:---|:---|:---|
| **P2.1 状态定义** | 新增 `rag/state/rag_state.py`  | 低 | 为图编排铺路 |
| **P2.2 项目结构** | 新增 `agents/graph/` + `tools/` 目录，搭建框架 | 低 | 为 Phase 3 Agent 做准备 |
| **P2.3 线性图** | 定义 StateGraph + 4 个节点，线性执行 | 低（行为不变） | 替代 chain.py |
| **P2.4 Service 层** | 新增 `services/agent_service.py`，统一管理图生命周期 | 低 | 符合分层规范，为中断/错误兜底做准备 |
| **P2.5 统一入口** | `api/chat.py` 调 `agent_service`，`agent_service` 调 `graph` | 低（封装变化） | 消除重复编排 |
| **P2.6 条件路由** | `add_conditional_edges` 基于 QueryRouter 结果分流 | 中 | 简单查询跳检索，降低延迟 |
| **P2.7 RetrievalGrader** | 检索后规则打分，低于阈值走 `query_rewriter` → 重检 | 中 | 检索质量闭环 |

### 2.2 新增/修改文件清单

```
src/
├── rag/
│   ├── state/                    # ★ 新增：Pipeline 状态层
│   │   ├── __init__.py
│   │   └── rag_state.py          # RAGState TypedDict
│   ├── chain.py                  # ○ 不变（仅 RAGAS eval 用，不走 graph）
│   ├── retrieval.py              # ○ 不变
│   ├── stream.py                 # ○ 不变
│   ├── prompt.py                 # ○ 不变
│   └── context.py                # ○ 不变
├── agents/                       # ★ 新增：Agent 定义（为 LangGraph 和 Phase 3 铺路）
│   ├── __init__.py
│   └── graph/                    # LangGraph 图定义
│       ├── __init__.py
│       ├── state.py              # AgentState（基于 RAGState 扩展）
│       ├── workflow.py           # StateGraph 组装（节点 + 边）
│       └── nodes.py              # 各节点函数（classify/rewrite/retrieve/rerank/generate）
├── tools/                        # ★ 新增：工具集（为 Agent 铺路）
│   ├── __init__.py               # 空目录，Phase 3 填充具体工具
│   └── base.py                   # ToolBase 抽象基类（预留）
├── api/
│   └── chat.py                   # ✎ 精简：仅 HTTP 校验 + 调 agent_service
├── services/
│   ├── agent_service.py          # ★ 新增：图生命周期管理（初始化/执行/中断/持久化）
│   └── chat_service.py           # ✎ 改造为 agent_service 的轻量包装，或删除
    ├── rag/
    │   ├── test_state.py         # ★ 新增
    │   └── test_pipeline.py      # ★ 新增
    └── agents/
        └── graph/
            └── test_graph.py     # ★ 新增：图节点单元测试
```

### 2.3 RAGState 定义

```python
# src/rag/state/rag_state.py

from typing import TypedDict, Optional, List


class RAGQueryIntent(TypedDict):
    route: str  # "simple" | "vague" | "complex" | "medium"
    rewritten: bool


class RAGContextItem(TypedDict):
    content: str
    source: str
    page: int
    doc_id: str
    chunk_id: str
    score: float


class RAGState(TypedDict, total=False):
    # 输入
    session_id: str
    kb_id: str
    query: str

    # 中间态
    intent: RAGQueryIntent                    # QueryRouter 分类结果
    rewritten_query: Optional[str]            # 改写后的查询
    retrieval_results: List[dict]             # 检索原始结果
    contexts: List[RAGContextItem]            # 精排后的上下文
    grader_score: Optional[float]             # RetrievalGrader 分数
    retrieval_retries: int                    # 重检次数（防死循环）

    # 输出
    answer: str
    citations: List[dict]

    # 可观测
    trace_id: str
    timings: dict                             # 各阶段耗时
```

### 2.4 图结构

```
                    START
                       │
               ┌───────▼───────┐
               │   classify     │    QueryRouter.route(query)
               │                │    → simple | medium | complex
               └───────┬───────┘
                       │
          ┌────────────┼────────────┐
          │ simple     │ medium     │ complex
          ▼            ▼            ▼
   ┌───────────┐  ┌───────────┐  ┌───────────┐
   │  generate │  │  rewrite  │  │  rewrite  │
   │  (直接 LLM) │  └─────┬─────┘  └─────┬─────┘
   │  无检索    │        │              │
   └─────┬─────┘   ┌─────▼─────┐  ┌─────▼─────┐
         │        │ retrieve  │  │ retrieve  │
         │        └─────┬─────┘  └─────┬─────┘
         │              │              │
         │        ┌─────▼─────┐  ┌─────▼─────┐
         │        │  rerank   │  │  grader    │
         │        └─────┬─────┘  └─────┬─────┘
         │              │         ┌────┴────┐
         │        ┌─────▼─────┐  │         │
         │        │ generate  │  │ 通过    │ 不通过
         │        └─────┬─────┘  │         │
         │              │   ┌────▼──┐  ┌───▼────┐
         │        ┌─────▼─┐ │ rerank│  │ rewrite│
         │        │format │ └───┬───┘  │ (重试) │
         │        └───┬───┘     │      └───┬────┘
         │            │    ┌────▼──┐       │
         │            │    │generate│       │
         │            │    └───┬───┘       │
         │            │        │           │
         │            │  ┌─────▼──┐        │
         │            │  │ format │        │
         │            │  └───┬───┘        │
         │            │      │            │
         └────────────┴──────┴────────────┘
                                 │
                                 ▼
                                END
```

**三级路由说明**：
- `classify → simple → generate` — Naive RAG：跳过检索，直接 LLM 回答。适合"单事实查询"如"2024年营收多少"
- `classify → medium → rewrite → retrieve → rerank → generate → format` — Enhanced RAG：混合检索+精排，无质量评估。适合"2-3个事实关联"如"近三年营收变化趋势"
- `classify → complex → rewrite → retrieve → grader → (通过) → rerank → generate → format` — Agentic RAG：全链路+质量闭环。适合"多跳推理"如"对比两家公司偿债能力并分析原因"
- `grader → (score < 阈值, retry < 2) → rewrite` — 重写查询后重新检索
- `grader → (score ≥ 阈值, 或 retry ≥ 2) → rerank` — 继续流水线



### 2.5 降级策略

复杂查询走 Agentic RAG 路径时，如果某环节失败，按以下层级降级，
保证始终返回结果而非报错：

```
complex path
    │
    ▼
grader 重试用尽
    │
    ▼
※ 降级到 Enhanced RAG（medium 路径）
   → rerank → generate
   （跳过 grader，用已有的检索结果）
   │
   ├─ rerank 有结果 → 正常生成回答
   │
   └─ rerank 结果为空
           │
           ▼
       ※ 降级到 Naive RAG（simple 路径）
          → 直接 LLM 回答
          （无检索上下文，靠模型自身知识）
          │
          └─ LLM 调用失败
                  │
                  ▼
              ※ 降级到错误消息
                 → "暂时无法回答，请稍后再试"
```

**实现方式**：
- `grader → (retry ≥ 2)` 已自动降级到 Enhanced RAG（不修改）
- `generate_node` 中检查 `contexts` 为空 → 调 `build_simple_prompt` 走 Naive RAG
- `agent_service.stream_chat` 捕获 LLM 异常 → 返回降级 SSE 事件
- `RAGState.downgraded: bool` 标记降级状态，方便日志排查

### 2.6 统一图调用



```python

# api/chat.py SSE 路径 —— 调 agent_service

async def _stream_rag_response(svc, kb_id, session_id, query):

    agent_svc = svc.agent_service

    async for event in agent_svc.stream_chat(kb_id, session_id, query):

        yield event

```



**agent_service.py 职责**：

1. 初始化 checkpointer，将 session_id 作为 thread_id 传给 config

2. 调 compiled_graph.astream_events() 执行图

3. 将 LangGraph 的 astream_events 转为自定义 SSE 事件

4. 捕获 GraphRecursionError / 工具超时，返回友好错误

5. 对话结束后持久化到 MySQL



> **注**：`chain.py` 仅 RAGAS eval 用，不走 graph。`chat_service.py` 改为 agent_service 的轻量包装或删除。
### 2.7 RetrievalGrader（规则版）

```python
# src/agents/grader.py

class RetrievalGrader:
    """
    规则版检索质量评分器。

    策略：
    1. 关键词覆盖度：查询中的关键 term 在前 N 个结果中出现的比例
    2. 结果稳定性：TOP_K_RERANK 与 TOP_K 的 score 分布
    3. 多样性惩罚：过多相似结果的扣分

    score ∈ [0, 1], 阈值 0.5
    """

    KEYWORD_TERM_MIN_LEN = 2

    def grade(self, query: str, results: list[dict], reranked: list[dict]) -> float:
        """
        返回质量分数 0~1。

        当前版本仅实现关键词覆盖度指标，
        后续可扩展为 LLM 评估（Phase 3）。
        """
        # 1. 提取 query 中有意义的关键词
        tokens = jieba.lcut(query)
        keywords = [
            t for t in tokens if len(t) >= self.KEYWORD_TERM_MIN_LEN
        ]

        if not keywords:
            return 0.8  # 无关键词时默认通过

        # 2. 检查精排后的 Top-N 是否覆盖关键词
        top_contents = [c["content"] for c in reranked[:TOP_K_RERANK]]
        if not top_contents:
            return 0.0

        covered = sum(
            1 for kw in keywords if any(kw in content for content in top_contents)
        )
        coverage = covered / len(keywords)

        return coverage
```

### 2.8 测试策略

```python
# tests/agents/graph/test_graph.py

class TestRAGGraph:
    """测试图的每个节点和条件边。"""

    def test_classify_node_simple(self):
        """simple 路由 → 应跳到 generate 跳过检索"""
        state = RAGState(query="2024年营收多少")
        result = classify_node(state)
        assert result["intent"]["route"] == "simple"

    def test_classify_node_complex(self):

    def test_classify_node_medium(self):
        """medium 路由 → 应走检索+精排，跳过 grader"""
        state = RAGState(query="对比A公司和B公司的偿债能力差异并分析原因")
        result = classify_node(state)
        assert result["intent"]["route"] == "medium"
        """complex 路由 → 应走完整链路"""
        state = RAGState(query="分析近三年营收变化趋势")
        result = classify_node(state)
        assert result["intent"]["route"] == "complex"

    def test_grader_high_score(self):
        """检索结果覆盖关键词 → score ≥ 0.5"""
        ...

    def test_grader_low_score(self):
        """检索结果不覆盖关键词 → score < 0.5"""
        ...

    def test_full_graph_linear(self):
        """线性路径：classify → retrieve → rerank → generate"""
        ...

    def test_simple_route_skip_retrieval(self):
        """simple 路径跳过检索，直接 generate"""
        ...
```

---

### 2.9 日志要求

每个节点函数必须包含 trace_id 出入日志，便于在图中追踪执行路径：

```python
def retrieve_node(state: RAGState) -> RAGState:
    trace_id = state.get("trace_id", "unknown")
    logger.info("[{}] retrieve_node start: query={} kb_id={}",
                trace_id, state["query"][:50], state.get("kb_id"))
    try:
        results = search(state["query"], state["kb_id"], vector_store, bm25)
        state["retrieval_results"] = results
        logger.info("[{}] retrieve_node done: results={}", trace_id, len(results))
        return state
    except Exception as e:
        logger.error("[{}] retrieve_node failed: {}", trace_id, e)
        raise
```

每个节点日志格式：`[trace_id] {node_name} {action}: {detail}`

trace_id 通过 `RAGState.trace_id` 传递，由 `agent_service` 在初始化 state 时注入。
这样通过一条 trace_id 可以串联整个图的执行过程。


## 三、Phase 3 详细规划

### 3.1 总体范围

| 里程碑 | 内容 | 前置依赖 |
|:---|:---|:---|
| **P3.1 FaithfulnessChecker** | 句子级忠实度检测，对比 context vs answer | Phase 2 图结构 |
| **P3.2 Reflection** | LLM 质量评分 + 多级路由 | P3.1 |
| **P3.3 Human-in-loop** | `interrupt_before` 人工介入 | P3.2 |

### 3.2 新增/修改文件

```
src/
├── rag/
│   └── pipeline/
│       ├── faithfulness.py      # ★ 新增：句子级忠实度检测
│       └── reflection.py        # ★ 新增：质量评分 + 路由决策
├── agents/
│   ├── grader.py                # ○ Phase 2 已有，Phase 3 增加 LLM 版
│   └── human_review.py          # ★ 新增：人工审核处理器
├── tools/                       # ★ Phase 2 已建目录，Phase 3 填充具体工具
│   ├── retrieval_tool.py        # ★ 新增：检索工具
│   └── calculator.py            # ★ 新增：计算工具
└── api/
    └── endpoints/
        └── review.py            # ★ 新增：人工审核 API 端点
```

### 3.3 Phase 3 图扩展

```
            ┌───────────────────────┐
            │   Phase 2 完整路径    │
            └───────────┬───────────┘
                        │
                  ┌─────▼──────┐
                  │ faithfulness│
                  │ _checker    │
                  └─────┬──────┘
                        │
              ┌─────────┼──────────┐
              │         │          │
         score≥0.7   score<0.7   score<0.7
              │         │     & retry耗尽
              │         ▼          │
              │  ┌──────────────┐  │
              │  │regenerate_agg│  │
              │  └──────┬───────┘  │
              │         │          │
              │         ▼          │
              │    (回 generate)   │
              │         │          │
              ▼         ▼          ▼
          ┌──────────────────────────┐
          │       reflection         │
          └────────────┬─────────────┘
                       │
         ┌─────────────┼──────────────┐
         │             │              │
     excellent      good         acceptable
         │             │         / poor
         │             │              │
         │             │        ┌─────▼──────┐
         │             │        │   retry    │
         │             │        │  (重做)    │
         │             │        └─────┬──────┘
         │             │              │
         │             │         (回 specialist)
         │             │              │
         ▼             ▼              ▼
    ┌─────────────────────────────────────┐
    │          human_review               │
    │          (interrupt_before)         │
    └─────────────────────────────────────┘
```

### 3.4 FaithfulnessChecker

```python
# src/rag/pipeline/faithfulness.py

class FaithfulnessChecker:
    """
    句子级忠实度检测（Self-RAG 风格）。

    从 context 和 answer 中提取事实性声明，
    逐句判断 answer 中的声明是否被 context 支持。
    """

    def check(self, contexts: list, answer: str) -> dict:
        """
        Returns:
            score: 0~1, <0.7 认为有幻觉风险
            suspicious: 可疑句子列表
            details: 逐句评估详情
        """
```

### 3.5 Reflection

```python
# src/rag/pipeline/reflection.py

class Reflection:
    """
    对整个 QA 过程做质量评审。

    评分等级：
    - excellent: 回答完整、准确、引用充分
    - good: 回答基本正确，有小幅改进空间
    - acceptable: 回答可用但不理想
    - poor: 回答不可用，需要重做
    """

    def review(self, query: str, contexts: list, answer: str) -> dict:
        """
        Returns:
            grade: "excellent" | "good" | "acceptable" | "poor"
            score: 0~1
            issues: 问题列表
        """

### 3.6 记忆系统规划

Phase 2 保持现有 ChatManager（Redis + 滑动窗口 + MySQL 持久化），
不引入 LangGraph Store / Checkpointer 等额外记忆机制。

Phase 3 引入 Human-in-loop 和 Reflection 时，可根据需要评估是否升级：

| 层级 | 组件 | 用途 | 引入时机 |
|:---|:---|:---|:---|
| **L1 — 会话级** | LangGraph Checkpointer + thread_id | 图执行状态自动快照，中断恢复 | Phase 3 Human-in-loop |
| **L2 — 持久化记忆** | LangGraph Store + Namespace | 跨会话用户偏好、事实知识 | 后续按需评估 |
| **L3 — 语义检索** | Store 配置 embed + dims | 语义搜索匹配 | 后续按需评估 |

详细调研见 `docs/openspec/changes/rag-orchestration-upgrade-phase2/memory-research.md`。

```

---


---

## 六、补充说明

### 6.1 测试兼容性 — test_chat.py 需更新 mock 路径

当前 `tests/api/test_chat.py` mock 了 `mock_svc.rag_chain.search/rerank/stream_answer`。Phase 2 后 SSE 路径改为调 `agent_service.stream_chat()`，测试需要改为：

```python
mock_agent = mock_svc.agent_service
mock_agent.stream_chat = fake_stream
```

影响文件：`tests/api/test_chat.py`

### 6.2 AppService 需挂载 agent_service

当前 `app_service.py` 没有 `agent_service` 属性。Phase 2 后 `api/chat.py` 访问 `svc.agent_service`，需要在 `AppService.__init__()` 中增加：

```python
self.agent_service = AgentService(self.rag_chain, ...)
```

影响文件：`src/services/app_service.py`

### 6.3 SSE 格式转换放在 agent_service 中

`sse_status/sse_token/sse_citation` 的调用从 `api/chat.py` 移到 `agent_service.stream_chat()` 内部。api 层只负责 `yield event`，不关心事件格式。

```python
# agent_service.py 内部
async def stream_chat(self, kb_id, session_id, query):
    async for event in graph.astream_events(...):
        if event_kind == "on_chain_start" and name == "retrieve":
            yield sse_status("retrieving", "正在检索...")
        elif event_kind == "on_chat_model_stream":
            yield sse_token(token)
        ...
    # format 节点完成后生成 citations
    for c in result["citations"]:
        yield sse_citation(...)
    yield sse_done()
```

### 6.4 Citation 去重+高亮逻辑移到 format 节点

当前 `api/chat.py:205-224` 的 citation 去重和 query-biased 高亮逻辑，Phase 2 后移到 `agents/graph/nodes.py` 的 `format` 节点中执行。`format` 节点的输出直接包含去重后的 citations 和 highlighted_snippet。

### 6.5 Checkpointer — Phase 2 暂不加

Phase 2 是线性图 + 条件路由，没有 interrupt_before 场景。checkpointer（MemorySaver）等到 Phase 3 引入 Human-in-loop 时再加，避免 Phase 2 引入不必要的复杂度。

### 6.6 依赖安装步骤

在 Phase 2 第一个 PR 中加入：

```toml
# pyproject.toml
"langgraph==1.2.9",
```

并在构建验证中确认 `langchain-core==1.4.8` 与 langgraph 1.2.9 兼容。


---

## 七、Grill Session 总结

本次设计审查（grill-me）共覆盖 9 个决策分支，全部达成一致：

| # | 分支 | 决策 |
|:---|:---|:---|
| 1 | 架构边界 | `rag/pipeline/` 去掉，`agents/graph/` 是唯一编排者 |
| 2 | 状态定义 | 合并成一个 `agents/graph/state.py`，去掉 `rag/state/` |
| 3 | 路由策略 | 二级改为三级：simple → medium → complex |
| 4 | SSE 位置 | SSE 转换放 `agent_service`，api 只转发 |
| 5 | Checkpointer | Phase 2 不加，Phase 3 再引入 |
| 6 | 降级策略 | 三层降级链：Agent → Enhanced RAG → Naive RAG → 错误消息 |
| 7 | AppService 注入 | 走 `Optional[AgentService] = None` 模式 |
| 8 | eval_ragas.py | Phase 2 不改，chain.py 保留 |
| 9 | PR 拆分 | 4 个 PR 依序推进 |

## 四、依赖变更

```toml
# pyproject.toml 新增
"langgraph==1.2.9"
```

---

## 五、阶段边界与验收标准

| 阶段 | 验收标准 |
|:---|:---|
| **Phase 2** | (1) `pytest tests/ -v` 全部通过<br>(2) SSE 流式行为与升级前一致（逐 token 推送、citation、done 事件）<br>(3) simple 路由跳过检索，延迟降低<br>(4) RetrievalGrader 能拦截低质量检索结果并触发重检 |
| **Phase 3** | (1) FaithfulnessChecker 能识别虚构内容并触发再生<br>(2) Reflection 能生成质量评分<br>(3) Human-in-loop 能正确中断并等待人工输入<br>(4) `pytest tests/ -v` 全部通过 |
| **保持不变** | 混合检索（ChromaDB + BM25 + RRF）不变<br>重排序（DashScopeRerank + 回退）不变<br>查询改写（expand/condense/decompose）包装为节点复用<br>SSE 前端事件格式不变 |
