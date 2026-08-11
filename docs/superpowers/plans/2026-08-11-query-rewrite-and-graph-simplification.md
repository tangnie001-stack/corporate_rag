# Query Rewrite & Graph Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 RAG 链路"反复追问/abstention"——独立 LLM 改写 + 双路径检索 + rerank 去阈值（LLM 判定 abstain）+ 删 grader + 批量澄清一次问完。

**Architecture:** 改造 LangGraph 检索链路（rewrite→retrieve→rerank→generate）：rewrite 从纯规则改为独立 flash LLM 调用并输出多查询；retrieve 并行多查询 N 路 RRF 合并；rerank 去掉绝对阈值改相对 top-N；abstention 下沉到 LLM 语义判断；删除 grader 死逻辑；澄清链路改为一次往返批量收集缺失维度（后端 SSE questions 列表 + 前端多问题表单）。

**Tech Stack:** Python 3.12 / LangGraph / FastAPI / ChromaDB / DashScope (qwen3.7-flash + qwen3.7-max + qwen3-rerank) / SSE / 原生前端 chat.html

## Global Constraints

- 层间调用规则：`api/` 不调 `infra/`/`config/`；`services/` 可调 `infra/`/`rag/`/`chat/`
- 代码风格：不用三元表达式（`a if cond else b`），写完整 if/else；显式类型检查（`x.attr if x is not None else default`）
- 所有函数写 docstring；dataclass 每个字段加行内注释
- 硬编码常量入 `src/config/`（prompts 文案入 prompts.py，阈值入 settings.py）
- 测试 mock 外部依赖（LLM/rerank/DB），不发起真实网络调用
- 质量门禁：`pytest tests/ -v` 全过、`ruff check .` 无错误、`pyright src/` 新增代码不引入新 error
- 单文件 >400 行拆模块，单函数 >80 行拆子函数
- RAGAS 4 项指标因测试集字面匹配虚高，**验收以难样本 abstain 率 + 幻觉率为准**（非 RAGAS）

---

### Task 1: 删除 AgentState grader 字段与常量

**Files:**
- Modify: `src/agents/graph/state.py:36-43, 72, 110-114, 176-179`
- Test: `tests/agents/graph/test_state.py`

**Interfaces:**
- Consumes: 无
- Produces: `AgentState` 删除字段 `grader_score`/`retrieval_retries`/`downgraded`/`downgrade_reason`/`_prev_rewritten_query`；`LangGraphNode.Grader` 类删除；`DOWNGRADE_REASON_REWRITE_NO_INCREMENT` 常量删除。后续任务依赖这些字段不存在。

- [ ] **Step 1: 更新失败测试**

修改 `tests/agents/graph/test_state.py`，删除 `test_agent_state_downgrade_fields`（断言 downgraded 字段不再存在）：

```python
def test_agent_state_no_grader_fields():
    state = AgentState(query="test")
    assert not hasattr(state, "grader_score")
    assert not hasattr(state, "retrieval_retries")
    assert not hasattr(state, "downgraded")
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/graph/test_state.py -v`
Expected: FAIL（字段仍存在）

- [ ] **Step 3: 实现**

在 `src/agents/graph/state.py`：
- 删除 `grader_score`、`retrieval_retries`、`downgraded`、`downgrade_reason`、`_prev_rewritten_query` 五个字段定义（36-43 行、72 行）
- 删除 `class Grader:`（110-114 行），含 `SCORE`/`RETRIEVAL_RETRIES`/`DOWNGRADED`/`DOWNGRADE_REASON`/`PREV_REWRITTEN_QUERY`
- 删除模块底部 `DOWNGRADE_REASON_REWRITE_NO_INCREMENT` 常量（176-179 行）

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/graph/test_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/graph/state.py tests/agents/graph/test_state.py
git commit -m "refactor: remove grader fields from AgentState"
```

### Task 2: 删除 grader_node 并直连 workflow

**Files:**
- Modify: `src/agents/graph/nodes.py:15-21, 173-214`
- Modify: `src/agents/graph/workflow.py:8-17, 35-55, 81, 108-118`
- Test: `tests/agents/graph/test_graph.py`

**Interfaces:**
- Consumes: Task 1（AgentState 无 grader 字段）
- Produces: `workflow.build_graph` 节点注册不含 grader，边为 `retrieve → rerank` 直连；`route_by_grader` 函数删除。

- [ ] **Step 1: 更新失败测试**

删除 `tests/agents/graph/test_graph.py` 中 3 个 grader_node 测试（`test_grader_node_short_circuit_on_first_fail`、`test_grader_node_short_circuit_when_rewrite_unchanged`、`test_grader_node_still_retries_when_rewrite_changed`）。新增图结构断言：

```python
def test_graph_no_grader_node():
    from src.agents.graph.workflow import build_graph
    from unittest.mock import MagicMock
    graph = build_graph(MagicMock(), None, MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())
    nodes = graph.get_graph().nodes
    assert "grader" not in nodes
    assert set(nodes) == {"kb_router", "classify", "rewrite", "retrieve", "rerank", "generate", "format"}
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/graph/test_graph.py::test_graph_no_grader_node -v`
Expected: FAIL（grader 仍在）

- [ ] **Step 3: 实现**

`src/agents/graph/nodes.py`：
- 删除 `grader_node` 函数（173-214 行）
- 删除 import：`from src.agents.grader import RetrievalGrader`、`DOWNGRADE_REASON_REWRITE_NO_INCREMENT`

`src/agents/graph/workflow.py`：
- import 列表删除 `grader_node`
- 删除 `route_by_grader` 函数（35-55 行）
- 删除 `builder.add_node(LangGraphNode.Grader.NAME, grader_node)`
- `retrieve → grader` 边改为 `retrieve → rerank` 直连：`builder.add_edge(LangGraphNode.Retrieve.NAME, LangGraphNode.Rerank.NAME)`
- 删除 grader 的 conditional edges（111-118 行）

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/graph/nodes.py src/agents/graph/workflow.py tests/agents/graph/test_graph.py
git commit -m "refactor: remove grader node, retrieve directly to rerank"
```

### Task 3: 清理 agent_service 与 eval_ragas 的 grader 引用

**Files:**
- Modify: `src/services/agent_service.py:100-107, 161-170, 255-261`
- Modify: `src/cli/eval_ragas.py:144-146`
- Test: `tests/services/test_agent_service.py`

**Interfaces:**
- Consumes: Task 1（无 downgraded 字段）
- Produces: `agent_service.stream_chat` 不再捕获 grader 事件；日志 `downgraded` 参数移除。

- [ ] **Step 1: 更新失败测试**

`tests/services/test_agent_service.py` 中若有断言 `downgraded` 相关输出，删除；若 mock graph 事件含 Grader 节点，移除。

- [ ] **Step 2: 实现**

`src/services/agent_service.py`：
- 删除 `downgraded = False`、`downgrade_reason = ""`（105-106 行）
- 删除 CHAIN_END 中 `elif LangGraphNode.Grader.NAME in name:` 分支（165-170 行）
- 日志（257 行）改为不含 `downgraded={} reason={}` 参数：`"AgentService stream_chat completed: total={:.1f}s contexts={}"`

`src/cli/eval_ragas.py`：
- 删除初始 state 中 `"retrieval_retries": 0, "downgraded": False, "downgrade_reason": ""` 三行（144-146 行）

- [ ] **Step 3: 删除 grader 模块与测试**

```bash
rm src/agents/grader.py tests/agents/graph/test_grader.py
```

- [ ] **Step 4: 验证**

Run: `pytest tests/ -v` + `grep -rn "grader\|downgraded\|retrieval_retries" src/ tests/`（应无残留）
Expected: 全过 + 无残留

- [ ] **Step 5: Commit**

```bash
git add -A src/services/agent_service.py src/cli/eval_ragas.py tests/
git commit -m "refactor: purge grader references from service and eval"
```

### Task 4: rerank_results 去绝对阈值

**Files:**
- Modify: `src/rag/retrieval.py:87-177`
- Test: `tests/rag/test_retrieval.py`（新增）

**Interfaces:**
- Consumes: 无
- Produces: `rerank_results(query, results, reranker) -> list[RAGContext]`，签名不变；不再用 `RERANK_MIN_SCORE` 过滤，改为取前 `TOP_K_RERANK` 条相对结果（rerank 失败 fallback 保持 raw order）。

- [ ] **Step 1: 写失败测试**

```python
# tests/rag/test_retrieval.py
import pytest
from src.rag.context import RAGContext
from src.rag.retrieval import rerank_results

def _cr(content, cid="c1"):
    return type("CR", (), {"content": content, "id": cid, "distance": 0.3,
        "metadata": {}, "bm25_score": None})()

def _mock_reranker(scores):
    class R:
        def rerank(self, docs, query):
            return [{"index": i, "relevance_score": s} for i, s in enumerate(scores)]
    return R()

def test_rerank_no_absolute_threshold():
    results = [_cr("a"), _cr("b")]
    # 两个低分但相对相关，旧逻辑会被 0.3 全过滤；新逻辑取 top2
    ctx = rerank_results("q", results, _mock_reranker([0.2, 0.15]))
    assert len(ctx) == 2  # 不再因 <0.3 过滤
    assert [c.score for c in ctx] == [0.2, 0.15]

def test_rerank_top_n_capped():
    results = [_cr("a"), _cr("b"), _cr("c")]
    ctx = rerank_results("q", results, _mock_reranker([0.9, 0.8, 0.7]))
    assert len(ctx) == min(3, 5)  # TOP_K_RERANK 上限
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/rag/test_retrieval.py -v`
Expected: FAIL（当前按 RERANK_MIN_SCORE 过滤，len=0）

- [ ] **Step 3: 实现**

`src/rag/retrieval.py` `rerank_results`：
- 删除 `apply_threshold` 变量及 `if apply_threshold and score < RERANK_MIN_SCORE: continue` 分支（143-150 行）
- 保留 `reranked[:TOP_K_RERANK]` 截断
- 删除 import 中 `RERANK_MIN_SCORE`（若不再使用）
- 删除 "Rerank filter" 日志分支

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/rag/test_retrieval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/rag/retrieval.py tests/rag/test_retrieval.py
git commit -m "feat: rerank uses relative top-N, drop absolute threshold"
```

### Task 5: rerank_node 打分策略（medium 原 query / complex 逐子查询）

**Files:**
- Modify: `src/agents/graph/nodes.py:217-230`（rerank_node）
- Test: `tests/agents/graph/test_graph.py`

**Interfaces:**
- Consumes: Task 4（rerank_results 去阈值）；`state.intent.route`、`state.query`、`state.rewritten_queries`、`state.retrieval_results`
- Produces: `rerank_node` 打分规则——medium 用 `state.query` 对合并候选池打一次分；complex 对 `state.rewritten_queries`（子查询）逐条打分合并，取各自 top 后去重合并。

- [ ] **Step 1: 写失败测试**

```python
def test_rerank_node_medium_uses_original_query():
    from src.agents.graph.nodes import make_rerank_node
    from src.agents.graph.state import AgentState, RAGQueryIntent

    def _cr(content, cid):
        return type("CR", (), {"content": content, "id": cid, "distance": 0.3,
                               "metadata": {}, "bm25_score": None})()
    calls = []
    class FakeReranker:
        def rerank(self, docs, query):
            calls.append(query)
            return [{"index": i, "relevance_score": 0.5 - i * 0.1} for i in range(len(docs))]
    state = AgentState(query="毛利率呢", intent=RAGQueryIntent(route="medium"),
                       rewritten_queries=["腾讯2024年毛利率是多少", "毛利率呢"],
                       retrieval_results=[_cr("毛利率数据", "c1"), _cr("营收数据", "c2")])
    node = make_rerank_node(FakeReranker())
    out = node(state)
    assert calls[0] == "毛利率呢"  # medium 用原 query 打分
    assert "contexts" in out
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/graph/test_graph.py::test_rerank_node_medium_uses_original_query -v`
Expected: FAIL（当前 rerank_node 用 `state.rewritten_query or state.query`，且无法断言 query）

- [ ] **Step 3: 实现**

`src/agents/graph/nodes.py` `make_rerank_node` 内部 `rerank_node`：

```python
def rerank_node(state: AgentState) -> dict:
    results = state.retrieval_results or []
    if not results:
        return {LangGraphNode.Rerank.CONTEXTS: []}
    contexts = []
    if (state.intent.route or "medium") == "complex" and state.rewritten_queries:
        # complex：逐子查询打分，各取 top 合并去重
        merged = []
        seen = set()
        for sub in state.rewritten_queries:
            for ctx in rerank_results(sub, results, reranker):
                if ctx.chunk_id in seen:
                    continue
                seen.add(ctx.chunk_id)
                merged.append(ctx)
        contexts = merged
    else:
        # medium：统一原 query 打分
        contexts = rerank_results(state.query, results, reranker)
    logger.info("rerank_node: contexts={}", len(contexts))
    return {LangGraphNode.Rerank.CONTEXTS: contexts}
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/graph/nodes.py tests/agents/graph/test_graph.py
git commit -m "feat: rerank scoring per route (medium original query, complex per sub-query)"
```

### Task 6: generate_node abstention 判定（检索空静态 / 非空 LLM）

**Files:**
- Modify: `src/agents/graph/nodes.py:236-278`（generate_node）
- Test: `tests/agents/graph/test_graph.py`

**Interfaces:**
- Consumes: Task 4/5（contexts 不再因阈值变空）
- Produces: `generate_node` — 检索空（`state.retrieval_results` 为空）→ 静态 `ABSTENTION_TEXT`；检索非空 → 正常 build_prompt 让 LLM 判断（不再有"contexts 空 → 静态 abstain"分支，因为去阈值后 contexts 恒非空）。

- [ ] **Step 1: 写失败测试**

```python
def test_generate_node_abstains_on_empty_retrieval():
    from src.agents.graph.nodes import make_generate_node
    from src.agents.graph.state import AgentState
    from src.config.prompts import ABSTENTION_TEXT
    state = AgentState(query="x", retrieval_results=[], contexts=[], skip_retrieval=False)
    node = make_generate_node(MagicMock(), MagicMock())
    out = node(state)
    assert out["answer"] == ABSTENTION_TEXT
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/graph/test_graph.py::test_generate_node_abstains_on_empty_retrieval -v`
Expected: FAIL 或现有 abstention 断言不匹配（现按 `contexts` 空判定，需改为按 `retrieval_results` 空判定）

- [ ] **Step 3: 实现**

`nodes.py` `generate_node` 第 ② 分支（260-278 行）判定条件改：

```python
# ② 检索无结果：abstention 静态文案（检索空才静态，非空由 LLM 判断）
if not (state.retrieval_results or []):
    ...保持原 abstention 逻辑（usage 估计 + ABSTENTION_TEXT）...
```

保留 `if not contexts:` 原逻辑删除——去阈值后 contexts 空只发生在检索空。同步确认 `ABSTENTION_MARKERS`/`ABSTENTION_TEXT` import 仍在。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/graph/nodes.py tests/agents/graph/test_graph.py
git commit -m "feat: abstain only on empty retrieval, defer judgement to LLM"
```

### Task 7: 新增 REWRITE prompts

**Files:**
- Modify: `src/config/prompts.py`
- Test: 无独立测试（prompt 常量由 Task 8 测试消费）

**Interfaces:**
- Produces: `REWRITE_SYSTEM_PROMPT: str`、`REWRITE_USER_TEMPLATE: str`（占位符 `{query}`/`{route}`/`{history}`，JSON 输出含 `standalone_query`/`sub_queries`）

- [ ] **Step 1: 实现**

在 `src/config/prompts.py` 新增（放在 Classifier Prompt 段之后）：

```python
# ====== Rewrite Prompt ======

# 查询改写系统提示词 — 独立单任务改写，与 classify 分离。
# 关键约束保护：只补历史明确约束，不篡改已有数字/公司/期间/否定。
REWRITE_SYSTEM_PROMPT: str = """你是一个查询改写专家。把当前用户问题改写为可独立检索的完整查询。

输入包含：
- 当前用户问题
- 路由类型：medium（单条改写）或 complex（多条子查询分解）
- 对话历史（多轮上下文，最近 2 轮）

任务：
- medium: 输出 standalone_query（单条，结合对话历史补全缺失约束）
- complex: 输出 sub_queries（2-4 条子查询，覆盖对比/多步分析的每个侧面）

规则（重要）：
- 只在对话历史明确提供了约束（年份/公司/期间）时，才将其补入改写查询
- 严禁修改用户问题中已有的数字、公司名、期间、否定词
- 保持原语言（中文），输出成句、可直接用于检索的完整查询
- 如用户问题含"分析/解释/说明/为什么"等口语化前缀，精简为可检索的查询
- 如果当前问题本身已完整，standalone_query 可原样返回

只返回 JSON，不要包含其他内容。"""

REWRITE_USER_TEMPLATE: str = """用户问题：{query}

路由类型：{route}

对话历史（最近2轮）：
{history}

输出 JSON（严格按此格式，改写字段仅对应路由输出）：
{{
  "standalone_query": "仅 medium 时输出；complex 省略",
  "sub_queries": ["仅 complex 时输出 2-4 条；medium 省略"]
}}
"""
```

- [ ] **Step 2: 验证**

Run: `python -c "from src.config.prompts import REWRITE_SYSTEM_PROMPT, REWRITE_USER_TEMPLATE; print(REWRITE_SYSTEM_PROMPT[:30])"`
Expected: 正常导入

- [ ] **Step 3: Commit**

```bash
git add src/config/prompts.py
git commit -m "feat: add independent rewrite prompts"
```

### Task 8: 实现 _llm_rewrite 与 fallback

**Files:**
- Modify: `src/infra/search/query_router.py`
- Modify: `src/config/prompts.py`（REWRITE prompt 已在 Task 7）
- Test: `tests/rag/test_rewrite.py`（新增）

**Interfaces:**
- Consumes: Task 7（REWRITE prompts）
- Produces: `_llm_rewrite(query: str, history: list, route: str) -> tuple[str | list[str], int, int]` — 返回 (改写结果, prompt_tokens, completion_tokens)。失败回退：LLM 异常/JSON 非法 → 规则 `rewrite_query`（现有）→ 原 query。放 `query_router.py` 模块级函数（或 `QueryRouter` 方法），复用 `_format_history` 与 token 统计模式。

- [ ] **Step 1: 写失败测试**

```python
# tests/rag/test_rewrite.py
import json
from unittest.mock import MagicMock
from src.infra.search.query_router import _llm_rewrite

def _resp(content, pt=10, ct=5):
    r = MagicMock()
    r.content = content
    r.response_metadata = {"token_usage": {"prompt_tokens": pt, "completion_tokens": ct}}
    return r

def test_llm_rewrite_medium_success():
    llm = MagicMock()
    llm.invoke.return_value = _resp(json.dumps({"standalone_query": "腾讯2024年毛利率是多少"}))
    result, pt, ct = _llm_rewrite("毛利率呢", [], "medium", llm)
    assert result == ["腾讯2024年毛利率是多少"]
    assert pt == 10

def test_llm_rewrite_complex_sub_queries():
    llm = MagicMock()
    llm.invoke.return_value = _resp(json.dumps({"sub_queries": ["腾讯利润", "东软利润"]}))
    result, _, _ = _llm_rewrite("对比腾讯东软利润", [], "complex", llm)
    assert result == ["腾讯利润", "东软利润"]

def test_llm_rewrite_fallback_on_bad_json():
    llm = MagicMock()
    llm.invoke.return_value = _resp("{bad json")
    result, _, _ = _llm_rewrite("对比腾讯东软利润", [], "complex", llm)
    assert isinstance(result, list) and result  # 回退到规则 decompose
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/rag/test_rewrite.py -v`
Expected: FAIL（`_llm_rewrite` 不存在）

- [ ] **Step 3: 实现**

`src/infra/search/query_router.py` 新增模块级函数：

```python
def _llm_rewrite(query, history, route, llm):
    """独立 LLM 查询改写，失败回退到规则改写。

    Args:
        query: 用户原始查询
        history: 对话历史（ChatMessage 列表）
        route: "medium" | "complex"
        llm: ChatOpenAI 实例（flash）

    Returns:
        (list[str] 改写查询列表, prompt_tokens, completion_tokens)；
        LLM 失败时回退规则改写结果（expand/condense/decompose），仍无效回退原 query
    """
    from langchain_core.messages import HumanMessage
    from src.config import CLASSIFIER_TEMPERATURE
    from src.config.prompts import REWRITE_SYSTEM_PROMPT, REWRITE_USER_TEMPLATE
    from src.rag.retrieval import rewrite_query

    history_text = _format_history(history)
    prompt = f"{REWRITE_SYSTEM_PROMPT}\n\n{REWRITE_USER_TEMPLATE.format(query=query, route=route, history=history_text or '无')}"
    try:
        response = llm.invoke([HumanMessage(content=prompt)], temperature=CLASSIFIER_TEMPERATURE)
        raw = (response.content or "").strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        data = json.loads(raw) if raw else {}
        # token 统计（复用 _llm_classify 的 metadata 读取模式）
        metadata = getattr(response, "response_metadata", {}) or {}
        usage = metadata.get("token_usage", {})
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        if pt is None or ct is None:
            usage_meta = getattr(response, "usage_metadata", None) or {}
            pt = usage_meta.get("input_tokens")
            ct = usage_meta.get("output_tokens")
        pt = int(pt or 0)
        ct = int(ct or 0)
        subs = data.get("sub_queries")
        if isinstance(subs, list):
            valid = [q for q in subs if isinstance(q, str) and q.strip()]
            if valid:
                return valid, pt, ct
        sq = data.get("standalone_query")
        if isinstance(sq, str) and sq.strip():
            return [sq], pt, ct
    except Exception:  # noqa: BLE001
        logger.warning("_llm_rewrite LLM failed, fallback to rules")
    # fallback：规则改写 → 原 query
    rule = rewrite_query(query, history, intent_route=route)
    if isinstance(rule, list):
        rule = [q for q in rule if q]
    else:
        rule = [rule] if rule else []
    return rule or [query], 0, 0
```

注：`_llm_rewrite` 内部 `from src.rag.retrieval import rewrite_query` 在函数内 import 避免循环依赖。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/rag/test_rewrite.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/infra/search/query_router.py tests/rag/test_rewrite.py
git commit -m "feat: independent LLM rewrite with rule fallback"
```

### Task 9: rewrite_node 改为工厂（触发条件 + 多查询输出）

**Files:**
- Modify: `src/agents/graph/nodes.py:123-141`（rewrite_node）
- Modify: `src/agents/graph/workflow.py:77`
- Modify: `src/agents/graph/state.py`（新增 `rewritten_queries`）
- Test: `tests/agents/graph/test_graph.py`

**Interfaces:**
- Consumes: Task 8（`_llm_rewrite`）；`state.intent.route`、`state.query`、`state._history`、`state.classification` 相关
- Produces: `make_rewrite_node(classify_llm) -> Callable`（async 节点）。输出 `rewritten_queries: list[str]`（改写结果 + 原 query 去重，原 query 必须保留）+ `rewritten_query: str`（medium=standalone_query[0]，complex=" ".join）。触发条件：complex 必触发；medium 仅当 history 非空 或 len<10 或 口语词（`呢/啊/吧`）或 含分析/解释/说明/为什么 或 classify 判定 query 不完整（`state.missing_entities` 非空）时触发；否则 `rewritten_queries = [state.query]`。

- [ ] **Step 1: state.py 新增字段**

`AgentState` 新增（中间态区）：
```python
rewritten_queries: list[str] = field(default_factory=list)  # rewrite_node 输出的检索查询列表（含原 query）
```
`LangGraphNode.Rewrite` 新增 `REWRITTEN_QUERIES: str = "rewritten_queries"`。

- [ ] **Step 2: 写失败测试**

```python
def test_rewrite_node_medium_short_query_calls_llm():
    import asyncio
    from unittest.mock import MagicMock, patch
    from src.agents.graph.nodes import make_rewrite_node
    from src.agents.graph.state import AgentState, RAGQueryIntent
    from src.infra.llm.chat_message import ChatMessage
    calls = []
    def fake_llm_rewrite(query, history, route, llm):
        calls.append((query, route))
        return ["腾讯2024年毛利率是多少"], 10, 5
    llm = MagicMock()
    with patch("src.infra.search.query_router._llm_rewrite", side_effect=fake_llm_rewrite):
        node = make_rewrite_node(llm)
        state = AgentState(query="毛利率呢", intent=RAGQueryIntent(route="medium"),
                           _history=[ChatMessage("user", "腾讯2024年营收多少")])
        out = asyncio.run(node(state))
    assert calls  # 触发了 LLM
    assert out["rewritten_queries"] == ["腾讯2024年毛利率是多少", "毛利率呢"]
    assert out["rewritten_query"] == "腾讯2024年毛利率是多少"
```

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/agents/graph/test_graph.py::test_rewrite_node_medium_short_query_calls_llm -v`
Expected: FAIL（`make_rewrite_node` 不存在）

- [ ] **Step 4: 实现**

`nodes.py` 将 `rewrite_node` 替换为工厂：

```python
def make_rewrite_node(classify_llm) -> Callable:
    """创建查询改写节点工厂函数（独立 LLM 改写）。"""
    from src.infra.search.entity_extractor import EntityExtractor
    from src.infra.search.query_router import _llm_rewrite

    _ORAL_WORDS = ("呢", "啊", "吧", "么")
    _ANALYSIS_WORDS = ("分析", "解释", "说明", "为什么")

    def _has_full_entities(query: str) -> bool:
        """查询是否已含关键实体（公司/期间/指标），组合消息视为完整。"""
        types = {e.type for e in EntityExtractor().extract(query)}
        return bool(types & {"company", "quarter", "year", "metric"})

    def _should_rewrite(state: AgentState) -> bool:
        route = state.intent.route or "medium"
        if route == "complex":
            return True
        query = state.query.strip()
        if len(query) < 10:
            return True
        if any(w in query for w in _ORAL_WORDS):
            return True
        if any(w in query for w in _ANALYSIS_WORDS):
            return True
        # 组合消息/完整查询已含关键实体且无省略词 → 跳过（避免批量澄清后误触发）
        if _has_full_entities(query):
            return False
        # 短省略查询且有多轮上下文 → 需 LLM 补全
        if state._history:
            return True
        return False

    async def rewrite_node(state: AgentState) -> dict:
        query = state.query
        route = state.intent.route or "medium"
        if not _should_rewrite(state):
            return {
                LangGraphNode.Rewrite.REWRITTEN_QUERIES: [query],
                LangGraphNode.Rewrite.REWRITTEN_QUERY: query,
            }
        rewritten, _, _ = await asyncio.to_thread(
            _llm_rewrite, query, state._history or [], route, classify_llm
        )
        queries = list(dict.fromkeys([*rewritten, query]))  # 去重保留原 query
        main_query = queries[0]
        result = {
            LangGraphNode.Rewrite.REWRITTEN_QUERIES: queries,
            LangGraphNode.Rewrite.REWRITTEN_QUERY: main_query,
        }
        if main_query != query:
            result[LangGraphNode.Classify.INTENT] = RAGQueryIntent(route=route, rewritten=True)
        logger.info("rewrite_node: {} -> {}", query[:30], queries)
        return result

    return rewrite_node
```

`workflow.py`：`builder.add_node(LangGraphNode.Rewrite.NAME, rewrite_node)` → `make_rewrite_node(classify_llm)`。

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agents/graph/nodes.py src/agents/graph/workflow.py src/agents/graph/state.py tests/agents/graph/test_graph.py
git commit -m "feat: rewrite node as async factory with LLM + multi-query output"
```

### Task 10: generalized N 路 RRF 合并

**Files:**
- Modify: `src/infra/search/bm25_index.py`
- Test: `tests/infra/search/test_bm25_index.py`

**Interfaces:**
- Consumes: 无
- Produces: `rrf_fusion_multi(results_groups: list[list[ChunkResult]], k: int = 60, top_n: int = 50) -> list[ChunkResult]` — 任意路 RRF 融合。

- [ ] **Step 1: 写失败测试**

```python
# tests/infra/search/test_bm25_index.py 追加
def test_rrf_fusion_multi_three_way():
    from src.infra.search.bm25_index import rrf_fusion_multi
    g1 = [_cr("a", "id1"), _cr("b", "id2")]
    g2 = [_cr("c", "id3"), _cr("a", "id1")]
    g3 = [_cr("b", "id2")]
    merged = rrf_fusion_multi([g1, g2, g3], k=60, top_n=5)
    assert merged[0].id in ("id1", "id2")  # 两路命中的排前
    assert {c.id for c in merged} == {"id1", "id2", "id3"}
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/infra/search/test_bm25_index.py -v`
Expected: FAIL（函数不存在）

- [ ] **Step 3: 实现**

`bm25_index.py` 在 `rrf_fusion` 后新增：

```python
def rrf_fusion_multi(
    results_groups: list[list[ChunkResult]],
    k: int = 60,
    top_n: int = 50,
) -> list[ChunkResult]:
    """任意路 RRF 融合多组检索结果。

    每路按排名贡献 1/(k+rank)，跨路累加后按得分降序取 top_n。

    Args:
        results_groups: 多组检索结果（每组一个查询的 dense 或 bm25 结果）
        k: RRF 排序常数（默认 60）
        top_n: 融合后保留的 top-N 结果数

    Returns:
        融合结果列表，按 RRF 得分降序，长度不超过 top_n
    """
    scores: dict[str, float] = {}
    data: dict[str, ChunkResult] = {}
    for group in results_groups:
        for rank, doc in enumerate(group):
            doc_id = doc.id
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
            if doc_id not in data:
                data[doc_id] = doc
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [data[doc_id] for doc_id, _ in ranked[:top_n]]
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/infra/search/test_bm25_index.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/infra/search/bm25_index.py tests/infra/search/test_bm25_index.py
git commit -m "feat: N-way RRF fusion for multi-query retrieval"
```

### Task 11: retrieve_node 并行多查询检索

**Files:**
- Modify: `src/agents/graph/nodes.py:144-170`（retrieve_node）
- Test: `tests/agents/graph/test_graph.py`

**Interfaces:**
- Consumes: Task 10（`rrf_fusion_multi`）；`state.rewritten_queries`
- Produces: `retrieve_node` — 并行（`asyncio.gather`）对 `state.rewritten_queries or [state.query]` 逐条 dense+BM25，多查询用 `rrf_fusion_multi` 合并；单查询直接返回。保留多 KB 分支。

- [ ] **Step 1: 写失败测试**

```python
def test_retrieve_node_multi_query_rrf():
    from src.agents.graph.nodes import make_retrieve_node
    from src.agents.graph.state import AgentState
    class FakeStore:
        def __init__(self):
            self.calls = []
        def similarity_search(self, kb_id, q, k):
            self.calls.append(q)
            return [type("CR", (), {"id": f"{q}_{i}", "content": q, "distance": 0.3,
                    "metadata": {}, "bm25_score": None})() for i in range(2)]
    store = FakeStore()
    node = make_retrieve_node(store, None)
    state = AgentState(query="毛利率呢", rewritten_queries=["腾讯2024年毛利率是多少", "毛利率呢"])
    out = asyncio.run(node(state))
    assert store.calls == ["腾讯2024年毛利率是多少", "毛利率呢"]  # 并行遍历
    assert len(out["retrieval_results"]) == 4
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/graph/test_graph.py::test_retrieve_node_multi_query_rrf -v`
Expected: FAIL（当前只用单 query）

- [ ] **Step 3: 实现**

`nodes.py` `make_retrieve_node` 内 `retrieve_node`：单 KB 分支改为：

```python
queries = state.rewritten_queries or [state.query]
if len(queries) > 1:
    async def _search_one(q):
        return await search(q, kb_id, vector_store, bm25)
    groups = await asyncio.gather(*[_search_one(q) for q in queries])
    results = rrf_fusion_multi([g for g in groups if g])
else:
    results = await search(queries[0], kb_id, vector_store, bm25)
```

多 KB 分支保持 `similarity_search_multi`（传入 `state.query`；如需多查询可在实现时扩展为逐 query 调用并合并）。import 增加 `from src.infra.search.bm25_index import rrf_fusion_multi`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/graph/nodes.py tests/agents/graph/test_graph.py
git commit -m "feat: parallel multi-query retrieval with N-way RRF"
```

### Task 12: classify 强化列出所有缺失实体

**Files:**
- Modify: `src/config/prompts.py:58-111`（CLASSIFIER_SYSTEM_PROMPT / CLASSIFIER_USER_TEMPLATE）
- Test: `tests/infra/search/test_query_router.py`

**Interfaces:**
- Produces: classify prompt 任务 2 明确"列出**所有**关键缺失实体（信息增益排序，最多 4 个）"；JSON schema 不变。

- [ ] **Step 1: 实现**

`CLASSIFIER_SYSTEM_PROMPT` 任务 2 修改为：
```
2. 补充缺失实体：检查 query 中是否缺少关键信息（年份、公司、指标等）；
   列出所有阻塞检索的关键缺失实体（按信息增益从高到低，最多 4 个）；
   能从对话历史或 KB 候选推断的不标记
```
`CLASSIFIER_USER_TEMPLATE` 的 `missing_entities` 示例保留，注释补充"可输出多个"。

- [ ] **Step 2: 验证**

Run: `pytest tests/infra/search/test_query_router.py -v`
Expected: PASS（现有测试不破坏）

- [ ] **Step 3: Commit**

```bash
git add src/config/prompts.py
git commit -m "feat: classifier lists all missing entities for batch clarification"
```

### Task 13: SSEClarificationEvent 多问题结构 + agent_service 批量澄清

**Files:**
- Modify: `src/utils/sse.py:61-77, 172-190, 224-227`
- Modify: `src/services/agent_service.py:180-203`
- Test: `tests/services/test_agent_service.py`

**Interfaces:**
- Consumes: Task 12（classify 输出多个 missing_entities）
- Produces: `SSEClarificationEvent` 新增 `questions: list[dict]`（每项 `{"type","question","suggestions"}`），保留 `question`/`missing_entities` 兼容；`sse_clarification` 序列化 questions。`agent_service` 构造 questions 列表。

- [ ] **Step 1: 写失败测试**

```python
def test_sse_clarification_serializes_questions():
    from src.utils.sse import sse_clarification, SSEClarificationEvent
    ev = SSEClarificationEvent(
        type="entity_completion",
        question="q1",
        missing_entities=[{"type": "company"}],
        suggestions=["东软"],
        questions=[{"type": "company", "question": "q1", "suggestions": ["东软"]},
                   {"type": "metric", "question": "q2", "suggestions": ["营收"]}],
    )
    s = sse_clarification(ev)
    assert '"questions"' in s
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: FAIL（questions 字段不存在）

- [ ] **Step 3: 实现**

`sse.py`：`SSEClarificationEvent` 加字段 `questions: list[dict] = field(default_factory=list)`；`sse_clarification` 构造 data 时含 `"questions": event.questions`（空则省略兼容）。

`agent_service.py`（180-203 行）：遍历全部 `missing_entities` 构造 questions：

```python
if _clarification_pending:
    cp = _clarification_pending
    from src.infra.search.query_router import SUGGESTIONS_MAP
    kb_suggestions = cp.get("suggestions") or {}
    questions = []
    for me in cp["missing_entities"]:
        etype = me.get("type", "default")
        sugg = kb_suggestions.get(etype) or SUGGESTIONS_MAP.get(etype, SUGGESTIONS_MAP["default"])
        questions.append({"type": etype, "question": me.get("question", "请补充相关信息"), "suggestions": sugg})
    first = questions[0]
    yield SSEClarificationEvent(
        type=cp["type"],
        question=first["question"],
        missing_entities=cp["missing_entities"],
        suggestions=first["suggestions"],
        questions=questions,
    )
    yield SSEDoneEvent()
    return
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/utils/sse.py src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "feat: batch clarification event with questions list"
```

### Task 14: agent_service abstention 引导

**Files:**
- Modify: `src/services/agent_service.py`（abstention 分支附近）
- Test: `tests/services/test_agent_service.py`

**Interfaces:**
- Consumes: Task 13（SSEClarificationEvent questions）
- Produces: 检索空 abstain 时，若 KB 有候选实体（`state._kb_entities` 非空/`suggestions` 非空），发送一次引导 clarification；否则纯文案。

- [ ] **Step 1: 写失败测试**

mock graph 返回空 contexts + 空 retrieval，KB suggestions 有值 → 期望产出 clarification 事件而非仅 token。

- [ ] **Step 2: 实现**

前置：`agent_service.py` classify 的 CHAIN_END 分支（145-160 行）需**无条件**保存 KB 候选到局部变量（当前只在 `missing_entities` 非空时设 `_clarification_pending`）：

```python
_kb_suggestions_all = output.get(LangGraphNode.Classify.KB_SUGGESTIONS) or {}
```

在 abstention 兜底分支（205-210 行）前插入：

```python
# abstention 引导：检索空且 KB 有候选时发一次 clarification
if not contexts and not _clarification_pending and _kb_suggestions_all:
    questions = [
        {"type": k, "question": "文档中未找到相关数据，可尝试查询：", "suggestions": v}
        for k, v in _kb_suggestions_all.items()
    ]
    yield SSEClarificationEvent(
        type="no_data_guidance",
        question="未在文档中找到相关数据，可尝试查询以下内容：",
        missing_entities=[], suggestions=[], questions=questions,
    )
    yield SSEDoneEvent()
    return
```

- [ ] **Step 3: 运行确认通过**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "feat: abstention guidance clarification"
```

### Task 15: 前端批量澄清多问题表单

**Files:**
- Modify: `frontend/chat.html`（`renderClarification` 684-739、`submitClarification` 741-753）

**Interfaces:**
- Consumes: Task 13（clarification 事件含 `questions`）
- Produces: 多问题表单渲染 + 组合提交（`startSSE(组合文本)`）

- [ ] **Step 1: 实现**

`renderClarification(clarEvent)` 改为：若 `clarEvent.questions` 存在，渲染多 section（每维度 question + chips + 输入框 + "其他"输入），底部"提交"按钮；否则保持单问题逻辑（兼容）。

`submitClarification` 改为收集所有答案：

```js
function renderClarification(clarEvent) {
  state.clarPending = clarEvent;
  const card = document.createElement('div');
  card.className = 'clarification-card';
  const questions = clarEvent.questions || [{
    type: '', question: clarEvent.question, suggestions: clarEvent.suggestions || []
  }];
  const sectionsHtml = questions.map((q, qi) => {
    const chips = (q.suggestions || []).map(s =>
      `<button class="chip${s === '其他' ? ' other' : ''}" data-qi="${qi}" data-value="${escapeHtml(s)}">${escapeHtml(s)}</button>`
    ).join('');
    return `<div class="clarification-section" data-qi="${qi}">
      <div class="clarification-question">${escapeHtml(q.question)}</div>
      <div class="suggestion-chips">${chips}</div>
      <input type="text" class="clarification-input" placeholder="输入补充信息…" data-qi="${qi}">
    </div>`;
  }).join('');
  card.innerHTML = `
    <div class="clarification-label">系统 · 缺少信息</div>
    <div class="clarification-inner">
      ${sectionsHtml}
      <button class="clarification-submit">提交</button>
    </div>`;
  // chip 点击：记录该维度选择（其他→聚焦输入框）
  card.querySelectorAll('.chip').forEach(chip => chip.addEventListener('click', () => {
    const qi = chip.dataset.qi; const v = chip.dataset.value;
    if (v === '其他') { card.querySelector(`.clarification-input[data-qi="${qi}"]`)?.focus(); return; }
    card.querySelector(`.clarification-input[data-qi="${qi}"]`).value = v;
  }));
  // 提交：组合所有已填维度
  card.querySelector('.clarification-submit').addEventListener('click', () => {
    const answers = [];
    card.querySelectorAll('.clarification-section').forEach(sec => {
      const v = sec.querySelector('.clarification-input').value.trim();
      if (v) answers.push(v);
    });
    if (answers.length) submitClarification(answers.join(' '));
  });
  chatContainer.appendChild(card);
  scrollToBottom();
}
```

- [ ] **Step 2: 验证**

启动服务，用 playwright-cli 或浏览器手动验证：澄清事件展示多 section，选"东软集团"+"毛利率"+"2025年第一季度"点提交 → 请求 query 为"东软集团 毛利率 2025年第一季度"。

- [ ] **Step 3: Commit**

```bash
git add frontend/chat.html
git commit -m "feat: batch clarification multi-question form"
```

### Task 16: 难样本回归验收

**Files:**
- Create: `src/cli/check_abstain.py`（难样本验收脚本，或复用 compare_rewrite 扩展）

**Interfaces:**
- Consumes: 全部任务
- Produces: 验收报告——难样本集（s1-s6 + session 轨迹）abstain 率、幻觉率、目标 chunk 命中。

- [ ] **Step 1: 写验收脚本**

构造样本集（query + history + 期望）：
- s2 `["腾讯2024年营收多少"], "毛利率呢"` → 期望命中（不 abstain）
- s5 `["介绍一下腾讯"], "他们的营收呢"` → 期望命中
- s6 `["2023年净利润多少"], "2024年呢"` → 期望命中
- s3 `[], "对比一下腾讯和东软的利润"` → 期望子查询命中两边
- s1 `["2025年第一季度营收多少"], "毛利率呢"` → 期望 abstain（数据缺失，正确行为）
- session 轨迹 `["本季度营收情况如何？"], "东软 毛利率 2025年第一季度"` → 期望一次往返（clarification questions ≥ 2 维度）

逐样本跑 graph，统计：
- abstain 率（目标 chunk 未命中且 LLM abstain）
- 幻觉率（LLM 回答但引用的 context 不含所问指标）
- 批量澄清 questions 数

- [ ] **Step 2: 运行与判定**

Run: `python -m src.cli.check_abstain`
判定：s2/s5/s6/s3 目标命中且不 abstain；s1 正确 abstain；session 场景 questions ≥ 2（一次往返）。幻觉率 = 0。

- [ ] **Step 3: 回归全量**

Run: `pytest tests/ -v` + `ruff check .` + `pyright src/`
Expected: 全过

- [ ] **Step 4: Commit**

```bash
git add src/cli/check_abstain.py
git commit -m "test: hard-sample abstain/hallucination acceptance"
```

---

## Self-Review

**Spec coverage:**
- `query-rewrite` spec（触发/约束保护/fallback）→ Task 7/8/9 ✅
- `multi-query-retrieval` spec（双路径/N 路 RRF/去阈值/打分策略）→ Task 4/5/10/11 ✅
- `retrieval-quality` spec（删 grader/LLM abstain）→ Task 1/2/3/6 ✅
- `batch-clarification` spec（批量澄清/防兜底/abstention 引导）→ Task 12/13/14/15 ✅
- 验收（难样本 abstain 率 + 幻觉率）→ Task 16 ✅

**已知实现期事项（不在本 plan 内做死，留实现确认）：**
- rerank API 输入上限：Task 11 的 `rrf_fusion_multi` 已含 `top_n=50` 截断，候选池不会超过 50 条进 rerank（RRF 合并后 50 条），无超限风险；如需更大候选可调整。
- complex context 上限：Task 5 合并后 context 数可能 >5，generate 直接消费，无硬上限；如需限制在实现时加截断。

**Type consistency：**
- `_llm_rewrite` 返回 `list[str]`（Task 8）与 `rewrite_node` 消费（Task 9）一致
- `rrf_fusion_multi(results_groups: list[list[ChunkResult]])`（Task 10）与 retrieve_node 调用（Task 11）一致
- `SSEClarificationEvent.questions: list[dict]`（Task 13）与前端消费（Task 15）一致
