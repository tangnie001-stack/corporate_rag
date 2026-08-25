# Agentic Clarification 阶段一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把澄清升级为 agent 自主的 ask_user 工具式交互（同一 turn 继续），删除 classify 与固定流水线，补齐 abort/并发/历史窗口/可观测性。

**Architecture:** LangGraph 1.2.9 手写 model↔tools 条件循环（`bind_tools` + ToolNode + 自定义条件边），图简化为 `kb_router → agent → tools → agent → … → agent_finalize → format`。ask_user 通过 SSE 推送问题、`POST /clarify-answer` 回传答案（挂起 Future + 注册表）。双路事件合并（graph task + SSE task 经 queue + 哨兵）。per-request 对象（澄清通道/abort 信号/挂起注册表/tool_contexts 收集器）经 contextvar 传递。

**Tech Stack:** Python 3.12 / LangGraph 1.2.9 / langchain-core 1.4.8 / FastAPI / Redis / loguru / pytest / httpx

**关联产物：** OpenSpec change `agentic-clarification`（proposal/design/specs/tasks 为准）；本计划是 tasks.md 阶段一部分的落地细化。阶段二（escalate_to_human + 工单产品层）**单独出计划**，不在本计划内。

## Global Constraints

- langgraph==1.2.9、langchain-core==1.4.8、langchain-openai==1.3.3（版本已锁定）
- 工具注入 state 用 `from langgraph.prebuilt import InjectedState`（**不是** `langchain_core.tools.InjectedState`，该版本不存在）
- 工具**不能通过 mutation 写 state**（state 更新只能由节点返回值 + reducer 生效）；工具内需写状态一律走 contextvar
- 所有函数写 docstring；dataclass 字段加行内注释
- 不用三元表达式；类型不确定的值用显式 if/isinstance
- 常量进 `src/config/`（settings.py 可配项走 os.getenv；const.py 固定阈值；prompts.py 文案/提示词）
- 测试 mock 外部依赖，不发起真实网络调用
- 接口契约变更同步更新 `docs/agents/api_contract.md`
- 层间调用规则：api/ 不得直接调 infra/ 或 config/

---

### Task 1: AgentState 改造（messages + tool_contexts + 护栏字段）

**Files:**
- Modify: `src/agents/graph/state.py`
- Test: `tests/agents/graph/test_state.py`（新建）

**Interfaces:**
- Produces: `AgentState.messages: Annotated[list[BaseMessage], add_messages]`、`AgentState.tool_contexts: list[RAGContext]`、`AgentState._agent_iterations: int`、`AgentState._max_agent_iterations: int`、`AgentState._ask_count: int`（仅日志用途）、删除 classify/rewrite/retrieve/rerank 相关字段
- Produces: `LangGraphNode` 仅保留 `KbRouter`/`Format`；`SSE_STATUS` 映射删除
- Consumes: `MAX_AGENT_ITERATIONS`（来自 `src/config/const.py`，Task 0 需先添加）

- [ ] **Step 0（前置）：添加护栏常量**

Modify `src/config/const.py` 追加：
```python
MAX_AGENT_ITERATIONS = 5  # agent 循环最大迭代数，超限强制收尾
MAX_ASK_PER_TURN = 2  # 单 turn 内 ask_user 最大调用次数
ASK_USER_TIMEOUT = 120  # ask_user 等待用户回答超时秒数
HISTORY_MAX_TURNS = 10  # 历史注入保留最近轮数
HISTORY_TOKEN_RATIO = 0.3  # 历史 token 占 context 窗口上限比例
SESSION_LOCK_TTL = 30  # per-session 并发锁 TTL 秒
```
若 const.py 已存在同类常量，合并进对应区域；`ASK_USER_TIMEOUT` 同时确认从 settings.py 可覆盖（`os.getenv`）。

- [ ] **Step 1: 写失败测试**

`tests/agents/graph/test_state.py`：
```python
from dataclasses import fields
from src.agents.graph.state import AgentState, LangGraphNode

def test_state_has_messages_with_addmessages_reducer():
    assert "messages" in {f.name for f in fields(AgentState)}
    # add_messages reducer 使 messages 支持追加（LangGraph 验证在 graph 测试中）

def test_state_has_agent_fields():
    assert "tool_contexts" in {f.name for f in fields(AgentState)}
    assert "_agent_iterations" in {f.name for f in fields(AgentState)}
    assert "_max_agent_iterations" in {f.name for f in fields(AgentState)}

def test_classify_fields_removed():
    names = {f.name for f in fields(AgentState)}
    assert "missing_entities" not in names
    assert "intent" not in names
    assert "rewritten_query" not in names
    assert "retrieval_results" not in names

def test_sse_status_removed():
    assert not hasattr(LangGraphNode, "SSE_STATUS") or SSE_STATUS is None  # noqa: F821
```
（`SSE_STATUS` 若从 state.py 删除则该断言改为 `from src.agents.graph.state import SSE_STATUS` 期望 ImportError——以实际实现为准写断言。）

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/graph/test_state.py -v`
Expected: FAIL（messages/tool_contexts 字段不存在）

- [ ] **Step 3: 实现**

`src/agents/graph/state.py`：
```python
from typing import Annotated
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from src.config.const import MAX_AGENT_ITERATIONS

@dataclass
class AgentState:
    """LangGraph 图执行状态（agentic 模式）。"""

    # ── 输入 ──
    session_id: str = ""  # 会话 ID
    kb_id: str = ""  # 知识库 ID（空=跨库）
    query: str = ""  # 用户原始查询
    trace_id: str = field(default_factory=lambda: current_trace_id.get() or "unknown")  # 链路追踪 ID
    # ── agent 循环 ──
    messages: Annotated[list[BaseMessage], add_messages] = field(default_factory=list)  # 模型可见消息（追加语义）
    tool_contexts: list[RAGContext] = field(default_factory=list)  # retrieve_kb 累积的检索上下文（引用溯源）
    _agent_iterations: int = 0  # 循环迭代计数
    _max_agent_iterations: int = MAX_AGENT_ITERATIONS  # 迭代上限
    _ask_count: int = 0  # 本 turn ask_user 调用次数（日志/兜底；实际检查走 contextvar）
    # ── 路由 ──
    _resolved_kb_ids: list[str] | None = None  # kb_router 解析的 KB ID 列表（None=未路由/全量）
    # ── 历史 ──
    _history: list[ChatMessage] = field(default_factory=list)  # Redis 会话历史（初始注入数据源）
    # ── 输出 ──
    answer: str = ""  # 最终回答（agent_finalize 写入）
    citations: list[dict] = field(default_factory=list)  # 去重引用列表（format 节点输出）
    model_used: str = ""  # 实际使用的模型名
    is_fallback: bool = False  # 是否触发模型 fallback
    # ── 内部 ──
    _token_usage: dict = field(default_factory=dict)  # token 用量统计
    timings: dict = field(default_factory=dict)  # 各节点耗时统计

    @classmethod
    def make_initial_state(cls, session_id, kb_id, query, history):
        return cls(session_id=session_id, kb_id=kb_id, query=query, _history=history)
```

删除：`RAGQueryIntent` 类（不再使用）、`LangGraphNode.Classify/Rewrite/Retrieve/Rerank/Generate` 嵌套类、`SSE_STATUS` 映射、`LangGraphEvent/LangGraphKey/LangGraph`（astream_events 相关常量保留则移到 agent_service，state.py 不保留）。保留 `LangGraphNode.KbRouter`、`LangGraphNode.Format`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/graph/test_state.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/config/const.py src/agents/graph/state.py tests/agents/graph/test_state.py
git commit -m "feat: 重构 AgentState 为 agentic 模式（messages/tool_contexts/护栏字段）"
```

---

### Task 2: contextvar 基建（per-request 对象）

**Files:**
- Create: `src/infra/llm/request_context.py`
- Test: `tests/infra/llm/test_request_context.py`（新建）

**Interfaces:**
- Produces: `RequestContext` 类（dataclass 持 queue/abort 信号/注册表/工具收集器），`current_request_ctx: ContextVar[RequestContext | None]`，`current_tool_contexts: ContextVar[list[RAGContext]]`
- Consumes: `RAGContext`（`src/rag/context.py`）

- [ ] **Step 1: 写失败测试**

```python
import asyncio
from src.infra.llm.request_context import current_request_ctx, current_tool_contexts

def test_contextvar_default_none():
    assert current_request_ctx.get() is None
    assert current_tool_contexts.get() == []

def test_set_and_reset():
    from src.infra.llm.request_context import RequestContext
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    assert current_request_ctx.get() is ctx
    current_request_ctx.reset(token)
    assert current_request_ctx.get() is None

def test_tool_contexts_accumulate():
    from src.infra.llm.request_context import RequestContext
    from src.rag.context import RAGContext
    with current_tool_contexts as _:  # noqa: 实际用 set/reset
        pass
    current_tool_contexts.get().append(RAGContext(...))
    assert len(current_tool_contexts.get()) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/infra/llm/test_request_context.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

`src/infra/llm/request_context.py`：
```python
"""Per-request 上下文 — 通过 contextvar 传递给 agent 循环内的工具与节点。

工具闭包在 AgentService 初始化时构建、跨请求共享，无法持有 per-request 对象；
graph 在同一 asyncio task 执行，contextvar 自动传播到工具与 async 节点。
"""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field

from src.rag.context import RAGContext


@dataclass
class RequestContext:
    """单次 /chat/stream 请求的共享对象集合。"""

    session_id: str  # 会话 ID
    clarify_channel: asyncio.Queue = field(default_factory=asyncio.Queue)  # 澄清事件/SSE 事件通道
    abort_signal: asyncio.Event = field(default_factory=asyncio.Event)  # 断连/取消信号
    registry: dict = field(default_factory=dict)  # session_id -> asyncio.Future（挂起澄清）
    tool_contexts: list[RAGContext] = field(default_factory=list)  # retrieve_kb 累积上下文（编号顺序即引用顺序）


current_request_ctx: ContextVar[RequestContext | None] = ContextVar("current_request_ctx", default=None)
"""当前请求共享对象；工具/节点经此读取 queue/abort/registry。"""

current_tool_contexts: ContextVar[list[RAGContext]] = ContextVar("current_tool_contexts", default=list())
"""（保留）兼容别名：指向 current_request_ctx 的 tool_contexts。"""
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/infra/llm/test_request_context.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/infra/llm/request_context.py tests/infra/llm/test_request_context.py
git commit -m "feat: 新增 per-request contextvar 基建"
```

---

### Task 3: retrieve_kb 工具（全局递增编号 + collector）

**Files:**
- Create: `src/agents/tools/rag_tools.py`
- Test: `tests/agents/tools/test_rag_tools.py`（新建）

**Interfaces:**
- Produces: `retrieve_kb(query: str, top_k: int, state: Annotated[AgentState, InjectedState()]) -> str`
- Consumes: `search()`/`rerank_results()`（`src/rag/retrieval.py`）、`format_context` 编号逻辑、`current_request_ctx`（contextvar，追加 `tool_contexts`）
- Notes: 全局递增编号——`offset = len(collector)`，返回 `[offset+1..offset+N]`；`offset 读取 + collector.extend` 必须在一个**无 await 的同步块**内完成（asyncio 单线程保证原子，并行工具调用编号不冲突）

- [ ] **Step 1: 写失败测试**

```python
import pytest
from langgraph.prebuilt import InjectedState  # noqa: F401（验证 import 路径）
from src.agents.tools.rag_tools import retrieve_kb
from src.agents.graph.state import AgentState

@pytest.mark.asyncio
async def test_retrieve_kb_global_numbering():
    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    state._resolved_kb_ids = ["kb1"]
    # mock _search_and_rerank 返回两条 context
    out1 = await retrieve_kb.invoke({"query": "毛利率", "state": state})
    out2 = await retrieve_kb.invoke({"query": "营收", "state": state})
    assert "[1]" in out1 and "[2]" in out1
    assert "[3]" in out2 and "[4]" in out2  # 第二轮从 3 开始，不冲突

@pytest.mark.asyncio
async def test_retrieve_kb_appends_to_collector():
    from src.infra.llm.request_context import current_request_ctx, RequestContext
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        await retrieve_kb.invoke({"query": "毛利率", "state": AgentState.make_initial_state("s1","kb1","q",[])})
        assert len(current_request_ctx.get().tool_contexts) >= 1
    finally:
        current_request_ctx.reset(token)
```
（`_search_and_rerank` 通过 monkeypatch 替换为返回固定 contexts；测试中 `retrieve_kb.invoke` 传 `{"query":..., "state":...}`——InjectedState 参数在直接 invoke 时需显式传入。）

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/tools/test_rag_tools.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

`src/agents/tools/rag_tools.py`：
```python
"""Agent 工具集 — retrieve_kb（检索）与 ask_user（澄清）。

工具经 contextvar 读 per-request 对象；经 langgraph.prebuilt.InjectedState 读 graph state。
工具不能写 state：tool_contexts 累积到 RequestContext.tool_contexts，由 agent_finalize 节点读入 state。
"""
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

from src.agents.graph.state import AgentState
from src.config import TOP_K_RERANK
from src.infra.llm.request_context import current_request_ctx
from src.rag.retrieval import rerank_results, search


class RetrieveKBArgs(BaseModel):
    query: str = Field(description="检索查询文本")
    top_k: int = Field(default=TOP_K_RERANK, ge=1, le=10, description="返回条数")


@tool("retrieve_kb", args_schema=RetrieveKBArgs)
async def retrieve_kb(
    query: str,
    top_k: int = TOP_K_RERANK,
    state: Annotated[AgentState, InjectedState()] = None,
) -> str:
    """在财务知识库检索与 query 相关的文档片段并精排。

    何时调用：问题涉及公司经营数据、财务指标、报告期等事实性内容时调用；
    闲聊、一般性概念问题不需要调用。
    """
    kb_ids = state._resolved_kb_ids if state is not None else None
    results = await search(query, kb_ids if isinstance(kb_ids, str) else None, None, None)  # 具体签名见 Task 3.1 适配
    contexts = rerank_results(query, results, reranker=None)  # reranker 注入见下方
    # 全局编号：同步块内读取偏移 + 追加，保证并行调用原子
    ctx = current_request_ctx.get()
    collector = ctx.tool_contexts if ctx is not None else []
    offset = len(collector)
    collector.extend(contexts)
    blocks = [f"[{offset + i + 1}] {c.to_prompt_text()}" for i, c in enumerate(contexts)]
    return "\n\n".join(blocks)
```

**Task 3.1 适配说明**：`search`/`rerank_results` 的真实签名与注入方式（vector_store/bm25/reranker 闭包）需在实现时对照 `src/rag/retrieval.py` 与 `src/rag/context.py` 完成——工具工厂 `make_rag_tools(vector_store, bm25, reranker, prompt_manager)` 返回工具列表，把依赖经闭包传入（闭包持有的是**共享依赖**，per-request 对象仍走 contextvar）。测试中用 monkeypatch 替换 `search`/`rerank_results` 返回固定 `[RAGContext(...), RAGContext(...)]`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/tools/test_rag_tools.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/tools/rag_tools.py tests/agents/tools/test_rag_tools.py
git commit -m "feat: retrieve_kb 工具（全局递增引用编号 + collector 累积）"
```

---

### Task 4: ask_user 工具（contextvar + Future + KB 注入 options）

**Files:**
- Modify: `src/agents/tools/rag_tools.py`
- Test: `tests/agents/tools/test_ask_user.py`（新建）

**Interfaces:**
- Produces: `ask_user(questions: list[AskQuestion], state: Annotated[AgentState, InjectedState()]) -> str`
- Consumes: `current_request_ctx`（channel/abort/registry）、`aggregate_kb_entities`（`src/infra/search/query_router.py:124`）、`SUGGESTIONS_MAP`、`MAX_ASK_PER_TURN`/`ASK_USER_TIMEOUT`
- Notes: **ask-count 检查/自增在工具内完成（工具不能写 state）**——用 `RequestContext` 上的可变计数（`ask_count` 字段），同步块内检查+自增。此机制修正设计 D12（迭代上限仍由节点走 state，ask 上限走 contextvar）。

- [ ] **Step 1: 写失败测试**

```python
import asyncio, pytest
from src.agents.tools.rag_tools import ask_user
from src.agents.graph.state import AgentState
from src.infra.llm.request_context import current_request_ctx, RequestContext

@pytest.mark.asyncio
async def test_ask_user_blocks_and_resolves():
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        task = asyncio.create_task(ask_user.invoke({"questions": [{"id":"q1","question":"哪一年？","dimension":"period","multi_select":False}], "state": AgentState.make_initial_state("s1","kb1","q",[])}))
        await asyncio.sleep(0.05)
        # 未回答前工具应阻塞（task 未完成）
        assert not task.done()
        # 模拟 POST /clarify-answer 解析 Future
        fut = ctx.registry.get("s1")
        fut.set_result({"answers":[{"id":"q1","selected":["2024年"]}]})
        result = await asyncio.wait_for(task, timeout=1)
        assert "2024年" in result
    finally:
        current_request_ctx.reset(token)

@pytest.mark.asyncio
async def test_ask_user_ask_limit():
    ctx = RequestContext(session_id="s1")
    ctx.ask_count = 2  # 已达上限
    token = current_request_ctx.set(ctx)
    try:
        result = await ask_user.invoke({"questions":[{"id":"q1","question":"哪一年？","dimension":"period","multi_select":False}], "state": AgentState.make_initial_state("s1","kb1","q",[])})
        assert "上限" in result
    finally:
        current_request_ctx.reset(token)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/tools/test_ask_user.py -v`
Expected: FAIL（ask_user 未定义 / RequestContext 缺 ask_count）

- [ ] **Step 3: 实现**

`RequestContext` 增加 `ask_count: int = 0` 字段（Task 2 文件同步修改）。

`rag_tools.py` 追加：
```python
class AskQuestion(BaseModel):
    id: str = Field(description="问题唯一 id，答案中回显")
    question: str = Field(description="问题文本")
    dimension: str = Field(default="free", description="缺失维度: company/period/metric/free")
    multi_select: bool = Field(default=False, description="是否多选")


class AskUserArgs(BaseModel):
    questions: list[AskQuestion]


@tool("ask_user", args_schema=AskUserArgs)
async def ask_user(
    questions: list[AskQuestion],
    state: Annotated[AgentState, InjectedState()] = None,
) -> str:
    """向用户询问补充信息后继续。

    何时调用：问题缺失关键实体（公司/期间/指标）且无法从上下文推断时调用；
    能回答就不要调用。问题选项由系统按维度从知识库注入真实候选。
    """
    ctx = current_request_ctx.get()
    if ctx is None:
        return "Error: 请求上下文不可用"
    if ctx.ask_count >= MAX_ASK_PER_TURN:  # 同步检查+自增，无 await
        return "Error: 已达本回合询问上限，请基于现有信息作答"
    ctx.ask_count += 1
    # KB 注入 options（同步获取，aggregate_kb_entities 为 async 时改 await）
    enriched = []
    for q in questions:
        options = await _load_dimension_options(q.dimension, state)  # KB 聚合/SUGGESTIONS_MAP
        enriched.append({"id": q.id, "question": q.question, "options": options, "multi_select": q.multi_select})
    # 推送问题（经 channel → SSE）+ 登记 Future
    await ctx.clarify_channel.put({"type": "ask_user", "questions": enriched})
    fut = asyncio.get_event_loop().create_future()
    ctx.registry[ctx.session_id] = fut
    try:
        answers = await _wait_with_abort_and_timeout(fut, ctx.abort_signal, ASK_USER_TIMEOUT)
        return str(answers)
    finally:
        ctx.registry.pop(ctx.session_id, None)
```

`_wait_with_abort_and_timeout`：`asyncio.wait` 竞争 fut/abort_signal.wait()/超时，返回答案 JSON 或超时/取消错误文本。`_load_dimension_options`：dimension∈{company,period,metric} 时查 `aggregate_kb_entities([kb_id])` 的候选实体填充 options；free 或无候选时用 `SUGGESTIONS_MAP` 或返回 `[]`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/tools/test_ask_user.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/tools/rag_tools.py src/infra/llm/request_context.py tests/agents/tools/test_ask_user.py
git commit -m "feat: ask_user 工具（挂起 Future + contextvar 计数 + KB 注入 options）"
```

---

### Task 5: make_agent_node（循环 + 初始注入 + answer 提取）

**Files:**
- Create: `src/agents/graph/agent_node.py`
- Test: `tests/agents/graph/test_agent_node.py`（新建）

**Interfaces:**
- Produces: `make_agent_model_node(llm, tools, prompt_manager) -> Callable`、`make_agent_tools_node(tools) -> Callable`、`make_agent_finalize_node() -> Callable`、`route_agent(state) -> str`
- Consumes: AgentState.messages/tool_contexts/_agent_iterations；`current_request_ctx.tool_contexts`；`build_prompt` 的历史转换逻辑（`src/rag/prompt.py`）
- Notes: 初始注入——messages 为空时把 system + 历史（`_history` 转 LangChain 消息）+ 当前 query 组装；迭代上限由 route_agent 检查 state._agent_iterations

- [ ] **Step 1: 写失败测试**

```python
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from src.agents.graph.agent_node import make_agent_finalize_node, route_agent
from src.agents.graph.state import AgentState
from src.infra.llm.request_context import current_request_ctx, RequestContext

@pytest.mark.asyncio
async def test_finalize_extracts_answer_and_contexts():
    ctx = RequestContext(session_id="s1")
    from src.rag.context import RAGContext
    ctx.tool_contexts.append(RAGContext(source="a.pdf", page=1, content="x", score=0.9))
    token = current_request_ctx.set(ctx)
    try:
        state = AgentState.make_initial_state("s1","kb1","q",[])
        state.messages = [HumanMessage(content="q"), AIMessage(content="答案是X [1]")]
        node = make_agent_finalize_node()
        out = await node(state)
        assert out["answer"] == "答案是X [1]"
        assert len(out["tool_contexts"]) == 1
    finally:
        current_request_ctx.reset(token)

def test_route_agent_tools_vs_finalize():
    state = AgentState.make_initial_state("s1","kb1","q",[])
    state.messages = [AIMessage(content="", tool_calls=[{"name":"retrieve_kb","args":{},"id":"c1","type":"tool_call"}])]
    assert route_agent(state) == "tools"
    state.messages = [AIMessage(content="直接回答")]
    assert route_agent(state) == "agent_finalize"
    state._agent_iterations = 99
    assert route_agent(state) == "agent_finalize"  # 超限强制收尾
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/graph/test_agent_node.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

`src/agents/graph/agent_node.py`：
```python
"""Agent 循环节点 — model↔tools 条件循环 + 收尾。

循环：agent_model（bind_tools 调用 LLM）→ route_agent → tools（ToolNode）→ 回 agent_model。
末轮无 tool_calls → agent_finalize（提取 answer + 读入 tool_contexts）→ format。
"""
from typing import Callable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import ToolNode

from src.agents.graph.state import AgentState
from src.infra.llm.request_context import current_request_ctx
from src.rag.prompt import build_prompt


def make_agent_model_node(llm, tools, prompt_manager) -> Callable:
    """创建 agent 模型节点工厂：bind_tools + 初始消息注入 + 迭代计数。"""
    model = llm.bind_tools(tools)

    def _initial_messages(state: AgentState) -> list[BaseMessage]:
        # 复用 build_prompt：system + 历史(ChatMessage→LangChain) + 当前 query
        return build_prompt(state.query, "", state._history or [], prompt_manager)

    async def agent_model(state: AgentState) -> dict:
        messages = state.messages if state.messages else _initial_messages(state)
        result = await model.ainvoke(messages)
        return {"messages": [result], "_agent_iterations": state._agent_iterations + 1}

    return agent_model


def make_agent_tools_node(tools) -> Callable:
    """创建工具节点：ToolNode 包装（handle_tool_errors 错误回喂）。"""
    node = ToolNode(tools, handle_tool_errors=True)

    async def agent_tools(state: AgentState) -> dict:
        return await node.ainvoke(state)

    return agent_tools


def make_agent_finalize_node() -> Callable:
    """创建收尾节点：提取末次 AIMessage 为 answer + 读入 tool_contexts。"""

    async def agent_finalize(state: AgentState) -> dict:
        last = state.messages[-1] if state.messages else None
        answer = _extract_text(last)
        ctx = current_request_ctx.get()
        contexts = ctx.tool_contexts if ctx is not None else []
        return {"answer": answer, "tool_contexts": contexts}

    return agent_finalize


def _extract_text(message: BaseMessage | None) -> str:
    """从 AIMessage 提取文本 content（str 或 content blocks）。"""
    if message is None:
        return ""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def route_agent(state: AgentState) -> str:
    """agent 条件边：有 tool_calls 且未超限 → tools；否则 → agent_finalize。"""
    if state._agent_iterations >= state._max_agent_iterations:
        return "agent_finalize"
    last = state.messages[-1] if state.messages else None
    if last is not None and getattr(last, "tool_calls", None):
        return "tools"
    return "agent_finalize"
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/graph/test_agent_node.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/agent_node.py tests/agents/graph/test_agent_node.py
git commit -m "feat: agent 循环节点（model↔tools + 收尾提取 answer/tool_contexts）"
```

---

### Task 6: workflow 简化 + 图集成测试

**Files:**
- Modify: `src/agents/graph/workflow.py`、`src/agents/graph/nodes.py`
- Test: `tests/agents/graph/test_graph.py`（更新）

**Interfaces:**
- Produces: `build_graph(vector_store, bm25, llm, classify_llm, reranker, embed_fn, prompt_manager, tools=None)`——图：`kb_router → agent → (tools|agent_finalize) → format → END`
- Consumes: Task 3/4 工具列表、Task 5 节点、`make_kb_router_node`、`format_node`
- Notes: `nodes.py` 删除 `make_classify_node`/`make_rewrite_node`/`make_retrieve_node`/`make_rerank_node`/`make_generate_node`；`make_kb_router_node` 与 `format_node` 保留

- [ ] **Step 1: 写失败测试**

```python
import pytest
from src.agents.graph.workflow import build_graph

def test_graph_topology():
    # 用 fake 依赖构建图，断言节点集合
    g = build_graph(vector_store=None, bm25=None, llm=object(), classify_llm=object(),
                    reranker=object(), embed_fn=object(), prompt_manager=object())
    nodes = set(g.get_graph().nodes.keys())
    assert "kb_router" in nodes
    assert "agent" in nodes and "tools" in nodes and "agent_finalize" in nodes
    assert "format" in nodes
    assert "classify" not in nodes and "rewrite" not in nodes
```
（`build_graph` 的依赖注入签名按实现适配；`tools` 参数默认由内部 `make_rag_tools` 构建。）

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/graph/test_graph.py::test_graph_topology -v`
Expected: FAIL（classify 仍在 / agent 节点缺失）

- [ ] **Step 3: 实现**

`workflow.py`：
```python
def build_graph(vector_store, bm25, llm, classify_llm, reranker, embed_fn, prompt_manager, tools=None) -> CompiledStateGraph:
    builder = StateGraph(AgentState)
    rag_tools = tools if tools is not None else make_rag_tools(vector_store, bm25, reranker, prompt_manager)
    builder.add_node(LangGraphNode.KbRouter.NAME, make_kb_router_node(embed_fn, classify_llm))
    builder.add_node("agent", make_agent_model_node(llm, rag_tools, prompt_manager))
    builder.add_node("tools", make_agent_tools_node(rag_tools))
    builder.add_node("agent_finalize", make_agent_finalize_node())
    builder.add_node(LangGraphNode.Format.NAME, format_node)
    builder.set_entry_point(LangGraphNode.KbRouter.NAME)
    builder.add_edge(LangGraphNode.KbRouter.NAME, "agent")
    builder.add_conditional_edges("agent", route_agent, {"tools": "tools", "agent_finalize": "agent_finalize"})
    builder.add_edge("tools", "agent")
    builder.add_edge("agent_finalize", LangGraphNode.Format.NAME)
    builder.add_edge(LangGraphNode.Format.NAME, END)
    return builder.compile()
```
`nodes.py` 删除 5 个工厂函数及其不再使用的 import（`build_prompt`/`build_simple_prompt`/`format_context`/`estimate_usage`/`stream_answer` 等随用随清）。`make_rag_tools` 在 `rag_tools.py` 中实现（返回 `[retrieve_kb, ask_user]`，经闭包注入共享依赖）。

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: PASS（拓扑断言 + 存量断言更新）

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/workflow.py src/agents/graph/nodes.py src/agents/tools/rag_tools.py tests/agents/graph/test_graph.py
git commit -m "feat: 图简化为 kb_router→agent 循环→format，删除固定流水线节点"
```

---

### Task 7: 挂起澄清注册表 + clarify-answer 端点

**Files:**
- Create: `src/api/clarify.py`
- Modify: `src/api/router.py`（或 app 注册处）
- Test: `tests/api/test_clarify.py`（新建）

**Interfaces:**
- Produces: `POST /api/chat/clarify-answer` body `{session_id: str, answers: list}`——resolve `RequestContext.registry[session_id]` 的 Future；查无返回 **404**
- Consumes: `current_request_ctx`（registry）
- Notes: 端点不依赖具体请求上下文实例——registry 是**进程级共享**的（因为 POST 与 SSE 是不同请求，contextvar 不共享）。**修正**：registry 不能只放 RequestContext，需进程级 `ClarifyRegistry` 单例（session_id → Future），SSE 请求登记、POST 解析。

- [ ] **Step 1: 写失败测试**

```python
import pytest
from src.api.clarify import clarify_registry

@pytest.mark.asyncio
async def test_register_and_resolve():
    fut = clarify_registry.register("s1")
    assert not fut.done()
    clarify_registry.resolve("s1", {"answers": [{"id": "q1", "selected": ["2024年"]}]})
    assert fut.done() and fut.result()["answers"][0]["selected"] == ["2024年"]

def test_resolve_missing_returns_404():
    assert clarify_registry.resolve("nope", {}) is None  # 端点层转 404
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_clarify.py -v`
Expected: FAIL（clarify_registry 不存在）

- [ ] **Step 3: 实现**

`src/api/clarify.py`：
```python
"""挂起澄清注册表 + /clarify-answer 端点。

SSE 请求（ask_user 工具）登记 Future，POST /clarify-answer 解析。
进程级单例（POST 与 SSE 是不同 HTTP 请求，contextvar 不跨请求）。
"""
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class ClarifyRegistry:
    """session_id -> asyncio.Future 的进程级注册表。"""

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future] = {}

    def register(self, session_id: str) -> asyncio.Future:
        fut = asyncio.get_event_loop().create_future()
        self._futures[session_id] = fut
        return fut

    def resolve(self, session_id: str, answers) -> bool:
        fut = self._futures.pop(session_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result(answers)
        return True

    def cancel(self, session_id: str) -> None:
        fut = self._futures.pop(session_id, None)
        if fut is not None and not fut.done():
            fut.set_exception(asyncio.CancelledError())


clarify_registry = ClarifyRegistry()


class ClarifyAnswerBody(BaseModel):
    session_id: str
    answers: list  # {id, selected, custom?}


@router.post("/chat/clarify-answer")
async def clarify_answer(body: ClarifyAnswerBody):
    """解析挂起的 ask_user Future；查无（超时/不存在）返回 404。"""
    ok = clarify_registry.resolve(body.session_id, body.answers)
    if not ok:
        raise HTTPException(status_code=404, detail="该澄清问题已超时或不存在")
    return {"code": 0, "data": True}
```
（响应包装按项目统一格式适配 `src/config/response_codes`。）

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/test_clarify.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/clarify.py tests/api/test_clarify.py
git commit -m "feat: 挂起澄清注册表与 clarify-answer 端点（404 超时语义）"
```

---

### Task 8: 双路 SSE 合并（哨兵收尾 + finally 联动）+ abort 信号

**Files:**
- Modify: `src/services/agent_service.py`、`src/api/chat.py`
- Test: `tests/services/test_agent_service.py`（更新）、`tests/services/test_dual_stream.py`（新建）

**Interfaces:**
- Produces: `AgentService.stream_chat(kb_id, session_id, query) -> AsyncGenerator[SSEEvent]`（内部 Task A 跑 graph 推 queue、Task B 消费）
- Consumes: `RequestContext`（queue/abort_signal）、`clarify_registry`、`SSEAskUserEvent`（Task 9）
- Notes: queue 哨兵（EndMarker/ErrorMarker）；Task B finally set abort + cancel task_a + gather

- [ ] **Step 1: 写失败测试（注入式双路单测）**

```python
import asyncio, pytest
from src.services.agent_service import _dual_stream  # 提取的可测纯函数

@pytest.mark.asyncio
async def test_dual_stream_sentinel_error():
    async def fake_events():
        yield {"event": "token"}
        raise ValueError("boom")
    events = []
    async for e in _dual_stream(fake_events(), asyncio.Queue(), timeout=2):
        events.append(e)
    assert any("error" in str(e).lower() for e in events)  # ErrorMarker → SSEErrorEvent

@pytest.mark.asyncio
async def test_dual_stream_cancel_propagates():
    cancelled = asyncio.Event()
    async def fake_events():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
    gen = _dual_stream(fake_events(), asyncio.Queue(), timeout=2)
    it = gen.__aiter__()
    await it.__anext__()  # 消费中
    await gen.aclose()    # 模拟 Task B 被取消
    assert cancelled.is_set()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/services/test_dual_stream.py -v`
Expected: FAIL（_dual_stream 不存在）

- [ ] **Step 3: 实现**

`agent_service.py`（提取纯函数 + 接入 stream_chat）：
```python
class _EndMarker: pass
class _ErrorMarker:
    def __init__(self, error: Exception) -> None:
        self.error = error


async def _dual_stream(event_source, queue: asyncio.Queue, abort_signal: asyncio.Event):
    """双路合并：Task A 跑事件源推 queue，Task B（本生成器）消费并 yield。"""
    async def run_source():
        try:
            async for ev in event_source:
                await queue.put(ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            await queue.put(_ErrorMarker(e))
        finally:
            await queue.put(_EndMarker())

    task_a = asyncio.create_task(run_source())
    try:
        while True:
            item = await queue.get()
            if isinstance(item, _EndMarker):
                break
            if isinstance(item, _ErrorMarker):
                yield SSEErrorEvent(f"暂时无法回答：{item.error}")  # noqa: 实际转事件对象
                break
            yield _convert_event(item)
    finally:
        abort_signal.set()
        task_a.cancel()
        await asyncio.gather(task_a, return_exceptions=True)
```

`stream_chat` 改造：
1. 创建 `RequestContext`（queue/abort_signal/registry 复用进程级 `clarify_registry`，tool_contexts 收集器）+ `current_request_ctx.set(...)`（用 token，finally reset）
2. 初始注入前对 `_history` 做历史窗口截断（Task 11）
3. 用 `_dual_stream(graph.astream_events(initial_state, version=LangGraph.VERSION), ctx.clarify_channel, ctx.abort_signal)` 作为事件源，并在外层处理 on_tool_start/on_chat_model_start/on_chat_model_end 状态事件（Task 12）与 model_used 捕获
4. 事件转换 `_convert_event`：LangGraph 事件 dict → SSE 事件（沿用现有 match kind 逻辑，删除 Generate/Classify 分支，加 on_tool_start 等）

`api/chat.py`：
- 创建 abort 检测：generator `finally` 里 set abort（由 `_dual_stream` 的 finally 完成）；`request.is_disconnected()` 轮询可选
- 并发锁：进入 stream_chat 前 `SETNX chat_lock:{session_id}`（TTL=SESSION_LOCK_TTL），冲突返回 409；finally 释放（Task 10）

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/services/test_dual_stream.py tests/services/test_agent_service.py -v`
Expected: PASS（双路哨兵/取消 + 存量 SSE 断言更新）

- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py src/api/chat.py tests/services/test_dual_stream.py tests/services/test_agent_service.py
git commit -m "feat: 双路 SSE 合并（哨兵收尾 + finally 联动 abort）"
```

---

### Task 9: SSE 事件类（SSEAskUserEvent/SSEAbstentionEvent）+ 状态事件按事件类型

**Files:**
- Modify: `src/utils/sse.py`
- Test: `tests/utils/test_sse.py`（新建）

**Interfaces:**
- Produces: `SSEAskUserEvent(questions: list)`、`SSEAbstentionEvent()`（转人工标记）、`SSEStatusEvent(stage, message)` 复用
- Removes: `SSEClarificationEvent` 标注退役（保留类定义兼容或删除，按 api_contract 决策）

- [ ] **Step 1: 写失败测试**

```python
from src.utils.sse import SSEAskUserEvent, SSEAbstentionEvent, to_sse

def test_ask_user_event_serializes():
    ev = SSEAskUserEvent(questions=[{"id":"q1","question":"哪一年？","options":["2024年"],"multi_select":False}])
    assert '"type": "ask_user"' in to_sse(ev)

def test_abstention_event():
    ev = SSEAbstentionEvent()
    assert '"type": "abstention"' in to_sse(ev)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/utils/test_sse.py -v`
Expected: FAIL（类不存在）

- [ ] **Step 3: 实现**

`src/utils/sse.py` 追加（按现有 dataclass + to_sse 模式）：
```python
@dataclass
class SSEAskUserEvent(SSEEvent):
    """ask_user 工具推送的问题卡片。"""
    type: str = "ask_user"
    questions: list = field(default_factory=list)  # [{id, question, options, multi_select}]


@dataclass
class SSEAbstentionEvent(SSEEvent):
    """abstention 标识 + 转人工标记。"""
    type: str = "abstention"
    message: str = "未在文档中找到相关数据，可尝试转人工咨询"
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/utils/test_sse.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/utils/sse.py tests/utils/test_sse.py
git commit -m "feat: SSEAskUserEvent/SSEAbstentionEvent 事件类"
```

---

### Task 10: per-session 并发锁

**Files:**
- Modify: `src/api/chat.py`
- Test: `tests/api/test_chat_lock.py`（新建）

**Interfaces:**
- Consumes: Redis（现有 `src/chat/manager.py` 的 Redis 客户端模式）、`SESSION_LOCK_TTL`

- [ ] **Step 1: 写失败测试**

```python
from unittest.mock import AsyncMock, MagicMock, patch
from src.api.chat import _acquire_session_lock, _release_session_lock

@pytest.mark.asyncio
async def test_lock_conflict():
    redis = MagicMock()
    redis.set.return_value = False  # SETNX 失败 → 已有锁
    assert not await _acquire_session_lock(redis, "s1")

@pytest.mark.asyncio
async def test_lock_acquire_release():
    redis = MagicMock()
    redis.set.return_value = True
    assert await _acquire_session_lock(redis, "s1")
    await _release_session_lock(redis, "s1")
    redis.delete.assert_awaited_once()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_chat_lock.py -v`
Expected: FAIL（函数不存在）

- [ ] **Step 3: 实现**

`api/chat.py`：
```python
async def _acquire_session_lock(redis, session_id: str) -> bool:
    """SETNX 获取 per-session 锁；返回是否成功。"""
    key = f"chat_lock:{session_id}"
    return bool(await redis.set(key, "1", nx=True, ex=SESSION_LOCK_TTL))


async def _release_session_lock(redis, session_id: str) -> None:
    await redis.delete(f"chat_lock:{session_id}")
```
在 `/chat/stream` 生成器入口获取、finally 释放；获取失败 `raise HTTPException(409, "当前会话正在处理中")`。Redis 客户端复用 `ChatManager` 的降级模式（不可用时跳过锁）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/test_chat_lock.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/chat.py tests/api/test_chat_lock.py
git commit -m "feat: per-session 并发锁（SETNX + TTL + 409）"
```

---

### Task 11: 历史窗口注入

**Files:**
- Modify: `src/agents/graph/agent_node.py`（初始注入前截断）
- Test: `tests/agents/graph/test_history_window.py`（新建）

**Interfaces:**
- Produces: `_truncate_history(history: list[ChatMessage], max_turns: int, token_ratio: float, context_window: int) -> list[ChatMessage]`
- Consumes: `HISTORY_MAX_TURNS`/`HISTORY_TOKEN_RATIO`

- [ ] **Step 1: 写失败测试**

```python
from src.agents.graph.agent_node import _truncate_history

def test_truncate_keeps_recent_turns():
    history = [ChatMessage(role="user", content=f"q{i}"), ChatMessage(role="assistant", content=f"a{i}") for i in range(15)]
    out = _truncate_history(history, max_turns=10, token_ratio=0.3, context_window=8000)
    assert len(out) <= 20  # 10 轮 * 2 条
    assert out[-1].content == "a14"  # 最近一条保留

def test_token_ratio_truncates_oldest():
    history = [ChatMessage(role="user", content="x" * 2000), ChatMessage(role="assistant", content="y" * 2000)] * 5
    out = _truncate_history(history, max_turns=10, token_ratio=0.3, context_window=8000)
    # 总长超 2400 token（8000*0.3）时从最旧截断
    assert len(out) < 10
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/graph/test_history_window.py -v`
Expected: FAIL（_truncate_history 不存在）

- [ ] **Step 3: 实现**

`agent_node.py` 追加：
```python
def _truncate_history(history, max_turns=HISTORY_MAX_TURNS, token_ratio=HISTORY_TOKEN_RATIO, context_window=8000):
    """历史窗口：保留最近 N 轮 + token 双上限，最近 1 轮完整保留。"""
    recent = history[-(max_turns * 2):] if len(history) > max_turns * 2 else history
    budget = int(context_window * token_ratio)
    # token 粗估：len//2（与 estimate_usage 一致）
    total = sum(len(m.content) // 2 for m in recent)
    while total > budget and len(recent) > 2:
        dropped = recent.pop(0)
        total -= len(dropped.content) // 2
    return recent
```
`_initial_messages` 调用前对 `state._history` 执行 `_truncate_history`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/graph/test_history_window.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/agent_node.py tests/agents/graph/test_history_window.py
git commit -m "feat: 历史窗口注入（最近 N 轮 + token 双上限）"
```

---

### Task 12: abstention 判定 + 状态事件接线（agent_service 事件处理）

**Files:**
- Modify: `src/services/agent_service.py`
- Test: `tests/services/test_agent_service.py`（更新）

**Interfaces:**
- Produces: `_is_abstention(state) -> bool`（tool_contexts 空 OR answer 匹配 `ABSTENTION_MARKERS`）
- Consumes: `SSEAbstentionEvent`、`SSEStatusEvent`、`ABSTENTION_MARKERS`（`src/config/prompts.py`）

- [ ] **Step 1: 写失败测试**

```python
from src.services.agent_service import _is_abstention
from src.agents.graph.state import AgentState

def test_abstention_empty_contexts():
    state = AgentState.make_initial_state("s1","kb1","q",[])
    state.answer = "未在文档中找到相关数据"
    assert _is_abstention(state)

def test_abstention_normal_answer():
    state = AgentState.make_initial_state("s1","kb1","q",[])
    state.tool_contexts.append(object())
    state.answer = "正常回答 [1]"
    assert not _is_abstention(state)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/services/test_agent_service.py::test_abstention -v`
Expected: FAIL（_is_abstention 不存在）

- [ ] **Step 3: 实现**

`agent_service.py`：
```python
def _is_abstention(state: AgentState) -> bool:
    """abstention 判定：无检索上下文 或 答案匹配拒答标记。"""
    if not state.tool_contexts:
        return True
    return any(marker in state.answer for marker in ABSTENTION_MARKERS)
```
双路事件转换 `_convert_event` 中：
- `on_chat_model_start`（metadata 含 langgraph_node="agent"）→ `yield SSEStatusEvent("agent", "正在思考...")`
- `on_tool_start` name="retrieve_kb" → `yield SSEStatusEvent("retrieve", "正在检索相关文档...")`；name="ask_user" → 不发
- `on_tool_end` name="retrieve_kb" → `yield SSEStatusEvent("retrieve", "检索完成，正在分析...")`
- `on_chat_model_end`（agent 节点）→ 捕获 model_used
- graph 正常结束后：`state = 最终 state`（astream_events 的 `on_chain_end` 或从 run 结果取），若 `_is_abstention(state)` → `yield SSEAbstentionEvent()` 再发 done
- 删除 `SSE_STATUS` 节点名匹配逻辑与 Generate/Classify CHAIN_END 捕获

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "feat: abstention 判定 + 状态事件按事件类型接线"
```

---

### Task 13: 前端 composer 化（chat.html）

**Files:**
- Modify: `frontend/chat.html`

**Interfaces:**
- Consumes: SSE 事件名 `ask_user`/`abstention`/`status`/`token`/`citation`/`done`；`POST /api/chat/clarify-answer`

- [ ] **Step 1: 后端事件先就位（Task 9/12 已交付），前端可 mock 验证**：先改监听器与提交逻辑（纯 JS 改动），用后端真实流联调

- [ ] **Step 2: 实现**

`chat.html`：
1. 删除 `clarification` 监听器（`startSSE` 内），新增 `ask_user` 监听器：
```javascript
source.addEventListener('ask_user', (e) => {
  try {
    const data = JSON.parse(e.data);
    // 不关流！state=CLARIFYING，隐藏输入区，渲染表单
    state.current = STATE.CLARIFYING;
    hideInputArea();
    renderComposer(data.questions || []);
  } catch (err) { /* ignore */ }
});
```
2. `renderComposer(questions)`：复用现有 `renderBatchClarification` 的 section 结构，但选项改 **radio/checkbox + label/description**（非 chip 点击即提交），底部"提交"按钮
3. `submitClarification(value)` → 改为结构化提交：
```javascript
function submitComposer(answers) {
  hideComposer();
  renderUserBubble(formatAnswers(answers));
  fetch('/api/chat/clarify-answer', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: state.sessionId, answers: answers}),
  }).then(res => {
    if (!res.ok) { showHint('该问题已超时，请重新提问'); restoreInputArea(); }
  }).catch(() => { showHint('提交失败，请重新提问'); restoreInputArea(); });
}
```
4. `done` 监听器：`state.current === STATE.CLARIFYING` 时 → `hideComposer()` + 提示"该问题已超时，请重新提问" + `restoreInputArea()`
5. 新增 `abstention` 监听器：abstention 消息旁显示"转人工咨询"按钮（点击记录会话标记，阶段一无工单）
6. 反馈：每条回答气泡下加 👍/👎 按钮 → `POST /api/feedback`

- [ ] **Step 3: 验证**：启动 `uvicorn src.main:app --reload`，浏览器打开 `frontend/chat.html`，验证：澄清表单接管输入区 → 提交后同流续答；abstention 显示转人工入口；done 恢复输入

- [ ] **Step 4: 提交**

```bash
git add frontend/chat.html
git commit -m "feat: 前端 composer 化（输入区接管 + 同流续答 + abstention 入口 + 反馈）"
```

---

### Task 14: 反馈接口

**Files:**
- Create: `src/api/feedback.py`
- Test: `tests/api/test_feedback.py`（新建）

**Interfaces:**
- Produces: `POST /api/feedback` body `{session_id, message_index, rating, comment?}`——落库（`feedback` 表或复用 messages 表，按现有持久化模式）
- Notes: rating ∈ {positive, negative}

- [ ] **Step 1: 写失败测试**

```python
from unittest.mock import AsyncMock, MagicMock
from src.api.feedback import _save_feedback

@pytest.mark.asyncio
async def test_save_feedback_positive():
    repo = AsyncMock()
    await _save_feedback(repo, session_id="s1", message_index=2, rating="positive", comment="准")
    repo.assert_called_once()  # 断言写入参数含 rating/comment
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_feedback.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**：`feedback.py` 端点 + `_save_feedback`（写 MySQL，失败仅记日志，与 `_persist_conversation` 的容错模式一致）。表结构与 `message_index` 语义对齐现有会话表（Open Question，实现时按 `docs/agents/api_contract.md` 定）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/api/test_feedback.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/feedback.py tests/api/test_feedback.py
git commit -m "feat: 答案反馈接口"
```

---

### Task 15: 契约与协调收尾

**Files:**
- Modify: `docs/agents/api_contract.md`
- 归档 `openspec/changes/query-rewrite-and-graph-simplification`

- [ ] **Step 1: 更新 api_contract.md**：新增 `POST /api/chat/clarify-answer`（404 语义）、`POST /api/feedback`、SSE 事件 `ask_user`/`abstention`；标注 `SSEClarificationEvent` 退役

- [ ] **Step 2: 归档 query-rewrite change**：`openspec archive-change query-rewrite-and-graph-simplification`（或按项目归档流程），其 rerank 去阈值/RRF 融合已在 Task 3 的 retrieve_kb 中吸收

- [ ] **Step 3: 全量回归**

Run: `pytest tests/ -v`、`ruff check src/`
Expected: 全绿；无遗留 print()/TODO

- [ ] **Step 4: 提交**

```bash
git add docs/agents/api_contract.md
git commit -m "docs: 更新接口契约，归档 query-rewrite change"
```

---

## Self-Review 备注

- **Spec 覆盖**：clarification-interaction（Task 3/4/7/9）、composer-ui（Task 13）、request-abort（Task 8）、human-escalation 阶段一入口（Task 12/13）、answer-feedback（Task 14）、agent-loop-observability（Task 5 日志埋点随节点实现 + Task 12 状态事件）、retrieval-quality delta（Task 12 `_is_abstention`）、历史窗口（Task 11）、并发锁（Task 10）
- **已知设计偏差**：D12 的"护栏计数存 AgentState"修正为——迭代上限走 AgentState（节点可写），ask 上限走 `RequestContext.ask_count`（工具不能写 state）；此偏差已在 Task 4 实现中体现，需同步回写 OpenSpec design.md
- **未覆盖（阶段二单独计划）**：escalate_to_human 工具、工单产品层、human-escalation 的 escal 护栏需求
