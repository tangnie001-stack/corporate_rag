# agent-loop-hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 agent 循环验证层——verify 包化（两态两管道）、kb_router 下移（含 KBRouter 死代码清理）、verify regen 决策化（修复空白答案 bug + 防死循环）、KB 输出护栏。

**Architecture:** 按依赖顺序分 4 个子改动，每步独立 commit + 全量回归：C1-5 verify 包化（纯重构地基，先做）→ C1-1 kb_router 下移（动图删节点 + 清死代码）→ C1-2 verify regen 决策化（bug 修复，verify 依据 agent 上一轮 search_web queries 覆盖度决策）→ C1-3 KB 输出护栏（加校验器）。verify 采用方案 A+：单图节点 + 两态校验器管道。

**Tech Stack:** Python 3.11+ / LangGraph / Loguru / pytest / ruff / pyright

## Global Constraints

- verify 拆包后旧模块路径 `src.agents.graph.verify_node` 被删除；`verify/__init__.py` re-export `verify_node`/`faithfulness_check`/`_ask_web_confirm`/`completeness_check`/`extract_years` 五名，消费方（workflow.py + test_verify_node.py）改 `from src.agents.graph.verify import (...)`，测试用例断言逻辑零改动
- 图拓扑只在 C1-1 动一次（删 kb_router 节点）
- `_agent_iterations` 只管 agent→tools 主循环（route_agent 强制收尾），不参与 verify regen 决策
- `_verify_regenerations` 上限 `MAX_VERIFY_REGENERATIONS=2`，仅作保险丝；正常由 verify 决策化提前终止
- 单文件 < 400 行红线；`pytest tests/ -v` 全过；`ruff check .` 无错误；`pyright src/` 不新增 error

---

### Task 1: C1-5 verify 包化（地基，纯重构）

**Files:**
- Create: `src/agents/graph/verify/` 目录 + `__init__.py` / `node.py` / `checks.py` / `guardrails.py` / `ask_confirm.py` / `faithfulness.py` / `pipeline.py`
- Modify: `src/agents/graph/workflow.py:16`（import 改为 `from src.agents.graph.verify import verify_node`）
- Modify: `tests/agents/graph/test_verify_node.py:15`（import 改为 `from src.agents.graph.verify import (...)` 五名，测试零逻辑改动仅 import 路径更新）
- Test: `tests/agents/graph/test_verify_node.py`（现有 22 用例 import 路径更新后零逻辑改动通过）

**Interfaces:**
- Consumes: `AgentState`（现有字段，`_resolved_kb_ids` 暂保留到 Task 2）、`RequestContext`
- Produces:
  - `verify/checks.py`: `extract_years(answer) -> set[int]`、`completeness_check(required, answer) -> list[int]`
  - `verify/faithfulness.py`: `async faithfulness_check(answer, contexts) -> list[str]`
  - `verify/ask_confirm.py`: `async _ask_web_confirm(state, missing_years) -> bool`
  - `verify/guardrails.py`: `web_citation_guard(state, ctx) -> dict | None`（态 A，Task 1 先搬原逻辑）
  - `verify/pipeline.py`: `async run_pipeline(pipeline, state, ctx) -> dict`（遍历校验器短路返回）
  - `verify/node.py`: `async verify_node(state) -> dict`（按 kb 分派两态管道）
  - `verify/__init__.py`: re-export `verify_node`/`faithfulness_check`/`_ask_web_confirm`/`completeness_check`/`extract_years` 五名

- [ ] **Step 1: 建包目录 + 空文件骨架**

Run: `mkdir -p src/agents/graph/verify`
Create 各文件（本步只建最小可 import 骨架）。

- [ ] **Step 2: 迁移 checks.py**

将 `verify_node.py` 的 `_YEAR_PATTERN` / `extract_years` / `completeness_check` 原样搬入 `verify/checks.py`（加 docstring），并删 verify_node.py 对应段。

- [ ] **Step 3: 迁移 faithfulness.py**

将 `faithfulness_check` 原样搬入 `verify/faithfulness.py`。

- [ ] **Step 4: 迁移 ask_confirm.py**

将 `_ask_web_confirm` 原样搬入 `verify/ask_confirm.py`（其依赖 `wait_with_abort_and_timeout` / `pending_asks` 从原模块导入）。

- [ ] **Step 5: 迁移 guardrails.py（web_citation_guard 态 A 引导）**

将 verify_node 态 A 分支（原文件 226-256 行）及其三个辅助函数抽出为校验器。完整实现：

```python
"""输出护栏校验器 — 联网引用引导（态 A）与 KB 强制溯源（态 B，Task 4 加）。"""

import re

from langchain_core.messages import SystemMessage
from loguru import logger

from src.agents.graph.state import AgentState
from src.config.const import (
    MAX_VERIFY_REGENERATIONS,
    SSEInteractionTexts,
    VERIFY_CITATION_MARKER,
)
from src.infra.llm.request_context import RequestContext


def _answer_has_citation(answer: str) -> bool:
    """回答是否含 [n] 引用标记。"""
    return re.search(r"\[\d+\]", answer) is not None


def _has_web_context(ctx) -> bool:
    """tool_contexts 是否含 kind=web 的联网上下文。"""
    if ctx is None:
        return False
    return any(
        getattr(c, "kind", None) == SSEInteractionTexts.CITATION_KIND_WEB
        for c in ctx.tool_contexts
    )


def _citation_guidance_already_injected(state: AgentState) -> bool:
    """查重：联网引用标注指引 SystemMessage 是否已注入过。"""
    return any(
        isinstance(m, SystemMessage) and VERIFY_CITATION_MARKER in (m.content or "")
        for m in state.messages
    )


async def web_citation_guard(
    state: AgentState, ctx: RequestContext | None
) -> dict | None:
    """态 A 联网引用引导：调过 search_web 但答案无 [n] → 注入指引重生成一次。

    Args:
        state: 当前图状态
        ctx: 请求上下文

    Returns:
        None 通过（未联网/已带引用/已达保险丝上限/已引导过）；
        注入指引的 regen 决策 dict
    """
    answer = state.answer or ""
    if (
        not _has_web_context(ctx)
        or _answer_has_citation(answer)
        or state._verify_regenerations >= MAX_VERIFY_REGENERATIONS
        or _citation_guidance_already_injected(state)
    ):
        return None
    logger.info(
        "verify web-citation guide session_id={} answer_len={}",
        state.session_id,
        len(answer),
    )
    guidance = SystemMessage(
        content=(
            f"你刚才的回答引用了联网搜索结果，但没有标注来源编号，"
            f"{VERIFY_CITATION_MARKER}，请在引用来源的对应句末补上 [n] 编号"
            "（编号须与搜索结果返回的来源列表一致）后重新回答。"
        )
    )
    return {
        "answer": answer,
        "messages": [guidance],
        "_needs_regenerate": True,
    }
```

注意：态 A 保险丝检查用 `_verify_regenerations`（Task 3 才加字段），故 Task 1 提交时 `MAX_VERIFY_REGENERATIONS` 与 `_verify_regenerations` 尚未存在——**实现顺序调整**：Task 1 先以 `state._agent_iterations < state._max_agent_iterations` 保持原行为，Task 3 Step 4 再换成 `_verify_regenerations` 计数。本 Step 的 `_verify_regenerations` 版本在 Task 3 后生效。

- [ ] **Step 6: 实现 pipeline.py**

```python
"""校验器管道执行器 — 遍历校验器列表，短路返回首个决策。"""

from collections.abc import Awaitable, Callable

from src.agents.graph.state import AgentState
from src.infra.llm.request_context import RequestContext

# 校验器协议：async (state, ctx) -> dict | None
#   None = 通过，继续下一校验器
#   dict = 决策（含 _needs_regenerate / messages / answer / _unsupported 等），短路返回
VerifyCheck = Callable[[AgentState, RequestContext | None], Awaitable[dict | None]]


async def run_pipeline(
    pipeline: list[VerifyCheck], state: AgentState, ctx: RequestContext | None
) -> dict:
    """遍历管道，首个返回非 None 的校验器短路。

    Args:
        pipeline: 校验器列表（有序）
        state: 当前图状态
        ctx: 请求上下文（可能 None）

    Returns:
        校验决策 dict；全部通过时返回直通决策 {"answer": ..., "_needs_regenerate": False}
    """
    for check in pipeline:
        decision = await check(state, ctx)
        if decision is not None:
            return decision
    return {"answer": state.answer or "", "_needs_regenerate": False}
```

- [ ] **Step 7: 实现 node.py（verify_node 按两态分派）**

```python
"""verify 节点主函数 — 按会话 kb 状态分派两态管道。"""

from loguru import logger

from src.agents.graph.state import AgentState
from src.agents.graph.verify.ask_confirm import _ask_web_confirm  # noqa: F401 (re-export)
from src.agents.graph.verify.checks import completeness_check, extract_years  # noqa: F401
from src.agents.graph.verify.faithfulness import faithfulness_check  # noqa: F401
from src.agents.graph.verify.guardrails import web_citation_guard
from src.agents.graph.verify.pipeline import run_pipeline
from src.config import settings
from src.infra.llm.request_context import RequestContext, current_request_ctx


async def _kb_pipeline_checks(ctx):
    """态 B（绑 KB）校验器列表 — completeness 决策化在 Task 3 接入，此处先返回最小列表。"""
    from src.agents.graph.verify.checks import completeness_check as _cc

    # Task 1 暂保持 P0 行为：完整性缺失联网在 node 内联实现（Task 3 再抽决策化）
    return None


async def verify_node(state: AgentState) -> dict:
    """验证循环节点：未绑定 KB 直通（态 A 联网引用引导）；绑定 KB 跑完整性/忠实度。

    Args:
        state: 当前图状态

    Returns:
        同原 verify_node 返回契约（{"answer", "messages", "_needs_regenerate", "_unsupported"}）
    """
    if not settings.VERIFY_ENABLED:
        return {"answer": state.answer or "", "_needs_regenerate": False}
    ctx = current_request_ctx.get()
    # ── 态 A：未绑定 KB（纯对话）──
    if not state._resolved_kb_ids:
        decision = await web_citation_guard(state, ctx)
        if decision is not None:
            return decision
        logger.info("verify skipped (no kb bound) session_id={}", state.session_id)
        return {"answer": state.answer or "", "_needs_regenerate": False}
    # ── 态 B：绑定 KB（Task 1 先内联保持原行为，Task 3 改走管道）──
    answer = state.answer or ""
    required = ctx.temporal_years if ctx is not None else []
    missing = completeness_check(required, answer) if required else []
    logger.info(
        "verify_node session_id={} kb_ids={} required={} missing={} answer_len={}",
        state.session_id,
        state._resolved_kb_ids,
        required,
        missing,
        len(answer),
    )
    if missing:
        # ── 态 B missing 分支（完整性缺失联网询问）──
        # 将原 verify_node.py 的 missing 处理整体搬入，行为一字不改：
        #   (a) ctx.web_confirmed 未置位 → await _ask_web_confirm(state, missing)
        #       - 确认 → ctx.web_confirmed = True
        #       - 拒绝/超时 → answer 追加"知识库仅覆盖 ...，缺失 ... 未联网补充"，
        #         return {"answer", "_needs_regenerate": False}
        #   (b) confirmed 或 web_confirmed → 查 _agent_iterations >= _max_agent_iterations
        #       - 超限 → 标注缺失直通 return
        #       - 未超限 → 查 VERIFY_GUIDANCE_MARKER 已注入（already_guided）
        #         · 已注入 → return {"answer", "_needs_regenerate": True}
        #         · 未注入 → 注入 SystemMessage（"知识库缺失年份...请调用 search_web"）
        #                   return {"answer", "messages": [guidance], "_needs_regenerate": True}
        # ── 参照源：原文件缺失处理即此处 (a)(b) 逻辑（Task 3 将整体替换为决策化）──
        raise NotImplementedError(
            "Step 7 必须把原 verify_node.py 的 missing 处理整体搬入本分支，"
            "不可留空；搬移后 Step 10 回归必须全绿"
        )
    contexts = ctx.tool_contexts if ctx is not None else []
    unsupported = await faithfulness_check(answer, contexts)
    if unsupported:
        return {"answer": answer, "_unsupported": unsupported, "_needs_regenerate": False}
    return {"answer": answer, "_needs_regenerate": False}
```

**说明**：Task 1 目标是**纯搬移零行为变化**——`verify_node` 态 B 的 missing 分支必须把原文件对应逻辑整体搬入（不重写），靠 `raise NotImplementedError` 强制执行者不得留空；态 A 分支抽成 `web_citation_guard`。Step 10 全量回归通过 = 搬移正确。Task 3 才把 missing 分支替换为决策化新逻辑。

- [ ] **Step 8: 实现 __init__.py re-export**

```python
"""verify 包 — 验证循环节点与校验器。

从 verify_node.py 单文件拆出；对 workflow.py 与测试保持顶层名不变。
"""

from src.agents.graph.verify.ask_confirm import _ask_web_confirm
from src.agents.graph.verify.checks import completeness_check, extract_years
from src.agents.graph.verify.faithfulness import faithfulness_check
from src.agents.graph.verify.node import verify_node

__all__ = [
    "verify_node",
    "faithfulness_check",
    "_ask_web_confirm",
    "completeness_check",
    "extract_years",
]
```

- [ ] **Step 9: 删除旧 verify_node.py + 更新 import**

Run: `rm src/agents/graph/verify_node.py`
同步更新两处 import（旧模块路径已删除，必须改）：
- `src/agents/graph/workflow.py:16` → `from src.agents.graph.verify import verify_node`
- `tests/agents/graph/test_verify_node.py:15` → `from src.agents.graph.verify import (_ask_web_confirm, completeness_check, extract_years, faithfulness_check, verify_node)`

**说明**：`verify/__init__.py` 的 re-export 只服务 `from src.agents.graph.verify import X`；旧路径 `from src.agents.graph.verify_node import X` 随文件删除必然 ImportError，必须同步改 import（测试用例断言逻辑零改动）。

- [ ] **Step 10: 运行全量测试**

Run: `pytest tests/agents/graph/ tests/agents/tools/ -v`
Expected: 全过（22 个 verify 用例 + graph/tools 用例零改通过，证明纯重构行为不变）

- [ ] **Step 11: Commit**

```bash
git add src/agents/graph/verify/ src/agents/graph/workflow.py
git commit -m "refactor: verify_node.py 拆 verify/ 包（两态分派 + re-export，行为不变）"
```

---

### Task 2: C1-1 kb_router 下移 + KBRouter 死代码清理

**Files:**
- Modify: `src/agents/graph/workflow.py`
- Modify: `src/agents/graph/nodes.py`
- Modify: `src/agents/graph/state.py`
- Modify: `src/agents/tools/rag_tools.py`
- Modify: `src/agents/tools/ask_tools.py`
- Delete: `src/rag/kb_router.py`
- Modify: `src/services/agent_service.py`（build_graph 调用去 embed_fn/classify_llm 死参）
- Test: `tests/agents/graph/test_graph.py` / `tests/agents/tools/test_rag_tools.py` / `tests/agents/tools/test_ask_user.py`

**Interfaces:**
- Consumes: Task 1 的 verify 包
- Produces: `AgentState.kb_id` 成为唯一检索判定源（`_resolved_kb_ids` 删除）；`build_graph` 签名不再含 embed_fn/classify_llm

- [ ] **Step 1: 更新 rag_tools.py 检索消费点**

`src/agents/tools/rag_tools.py` retrieve_kb 内（约 98-132 行）——`kb_ids` 来源从 `state._resolved_kb_ids` 换为 `state.kb_id`：

```python
        if state is not None:
            kb_id = state.kb_id
        else:
            kb_id = ""
        # 时间解析：仅绑定 KB 且命中时间词才执行
        ctx = current_request_ctx.get()
        if (
            settings.TEMPORAL_PARSE_ENABLED
            and ctx is not None
            and kb_id
            and has_temporal_words(query)
            and not ctx.temporal_parsed
        ):
            candidates = await derive_candidate_years([kb_id])
            from src.models import get_classify_llm

            parsed = await parse_temporal(query, candidates, get_classify_llm())
            ctx.temporal_years = parsed["years"]
            ctx.missing_years = compute_missing(parsed["years"], candidates)
            ctx.temporal_parsed = True
        # 检索：kb_id 非空 → 检索该库；空（未绑定）→ 不检索
        if kb_id:
            per_kb_results = await asyncio.gather(
                retrieval.search(query, kb_id, vector_store, bm25)
            )
            results = _merge_search_results(per_kb_results)
        else:
            results = []
```

- [ ] **Step 2: 更新 ask_tools.py 消费点**

`src/agents/tools/ask_tools.py` `_load_dimension_options`（约 165-171 行）——去掉 `_resolved_kb_ids` 分支：

```python
    if dimension in ("company", "period"):
        if state is not None and state.kb_id:
            kb_ids = [state.kb_id]
        else:
            kb_ids = None
        aggregate = await _aggregate_entities(kb_ids)
        if dimension == "company":
            candidates = aggregate.companies
        else:
            candidates = aggregate.periods
        if candidates:
            return list(candidates)
    return SUGGESTIONS_MAP.get(dimension, [])
```

- [ ] **Step 3: 更新 state.py**

`src/agents/graph/state.py`：
- 删 `_resolved_kb_ids: list[str] | None = None` 字段（44-46 行）
- 删 `LangGraphNode.KbRouter` 嵌套类（85-87 行）

- [ ] **Step 4: 更新 workflow.py 删节点 + 签名**

```python
# workflow.py 改动：
# 1. import 去掉 make_kb_router_node、LangGraphNode.KbRouter
# 2. build_graph 签名去掉 embed_fn/classify_llm 参数
# 3. 删 builder.add_node(KbRouter...)、set_entry_point 改 "agent"
# 4. add_edge(KbRouter → agent) 删除
# 5. 编译日志改 "agent 循环 → verify → format"
def build_graph(
    vector_store: VectorStore,
    bm25: BM25Index | None,
    llm,
    reranker,
    prompt_manager,
    tools=None,
) -> CompiledStateGraph:
    """构建并编译 agent 循环图：agent → (tools|agent_finalize) → verify → format → END。"""
    builder = StateGraph(AgentState)
    if tools is not None:
        rag_tools = tools
    else:
        rag_tools = make_rag_tools(vector_store, bm25, reranker, prompt_manager)
    builder.add_node("agent", make_agent_model_node(llm, rag_tools, prompt_manager))
    builder.add_node("tools", make_agent_tools_node(rag_tools))
    builder.add_node("agent_finalize", make_agent_finalize_node())
    builder.add_node("verify", verify_node)
    builder.add_node(LangGraphNode.Format.NAME, format_node)
    builder.set_entry_point("agent")
    builder.add_conditional_edges(
        "agent", route_agent, {"tools": "tools", "agent_finalize": "agent_finalize"}
    )
    builder.add_edge("tools", "agent")
    builder.add_edge("agent_finalize", "verify")
    builder.add_conditional_edges(
        "verify", route_verify, {"agent": "agent", "format": LangGraphNode.Format.NAME}
    )
    builder.add_edge(LangGraphNode.Format.NAME, END)
    graph = builder.compile()
    logger.info("LangGraph StateGraph compiled: agent 循环 → verify → format")
    return graph
```

同步 `make_rag_tools` 签名去掉 embed_fn 死参（rag_tools.py:58-77）：`make_rag_tools(vector_store, bm25, reranker, prompt_manager)`（embed_fn 仅在旧 kb_router 用，现死参删除）。

- [ ] **Step 5: 更新 nodes.py 删 make_kb_router_node**

`src/agents/graph/nodes.py`：删 `make_kb_router_node` 函数（22-38 行）及其 docstring。

- [ ] **Step 6: 删 KBRouter 死代码 + 清 agent_service 死参**

Run: `rm src/rag/kb_router.py`
`src/services/agent_service.py`：
- import 去 `make_kb_router_node` 相关
- `build_graph(...)` 调用去掉 `embed_fn`/`classify_llm` 实参（原 502 行附近）
- 若 `self._classify_llm`/`self._embed_fn` 无其他用途则一并删除初始化（保留有用途的）

同步清理 `models.py:181` / `settings.py:46` 中 KBRouter 相关注释（改为"已删除，无路由模型"）。

- [ ] **Step 7: 更新测试断言**

- `tests/agents/graph/test_graph.py` `test_graph_topology`：节点集合从 `{"kb_router","agent","tools","agent_finalize","verify","format"}` 改为去掉 kb_router
- `test_rag_tools.py` / `test_ask_user.py`：任何构造 `AgentState(_resolved_kb_ids=...)` 或断言引用 `_resolved_kb_ids` 的用例改 `kb_id`
- `build_graph` 相关测试调用签名同步去掉 embed_fn/classify_llm 参数

- [ ] **Step 8: 全量回归**

Run: `pytest tests/ -v && ruff check src/agents/ src/rag/ src/services/agent_service.py && pyright src/agents/graph/workflow.py`
Expected: 全过 / 无 error

- [ ] **Step 9: Commit**

```bash
git add -A src/agents/ src/rag/ src/services/agent_service.py tests/agents/
git commit -m "refactor: kb_router 下移（删节点+_resolved_kb_ids 退役）+ KBRouter 死代码清理"
```

---

### Task 3: C1-2 verify regen 决策化

**Files:**
- Modify: `src/agents/graph/state.py`（加 `_verify_regenerations`）
- Modify: `src/config/const.py`（加 `MAX_VERIFY_REGENERATIONS`）
- Modify: `src/agents/graph/verify/node.py`（态 B 决策化）
- Modify: `src/agents/graph/verify/guardrails.py`（态 A 引导同步换计数）
- Test: `tests/agents/graph/test_verify_node.py` / `tests/agents/graph/test_graph.py`（新增决策化图集成测试）

**Interfaces:**
- Consumes: Task 1-2 产物
- Produces: `AgentState._verify_regenerations`（保险丝）；态 B 完整性缺失联网走决策化（看 agent 上一轮 search_web queries）

- [ ] **Step 1: 加字段与常量**

`src/agents/graph/state.py` 加：

```python
    _verify_regenerations: int = (
        0  # verify 修订保险丝计数（来源：verify 决策化兜底；范围：单轮执行；用途：防 verify→agent 无限往返，正常被决策化提前终止）
    )
```

`src/config/const.py` 加：

```python
# verify 修订保险丝上限：正常被决策化（看 agent 上一轮 search_web queries）提前终止，
# 仅在 agent 反复不按指引执行时兜底防死循环
MAX_VERIFY_REGENERATIONS = 2
```

- [ ] **Step 2: 实现决策判定辅助函数**

`src/agents/graph/verify/checks.py` 加（态 B 决策化核心，读 messages 中最近 search_web tool_call）：

```python
from langchain_core.messages import AIMessage


def last_search_web_queries(messages) -> list[str] | None:
    """返回 messages 中最近一次 search_web 工具调用的 queries 参数。

    Args:
        messages: state.messages（langchain BaseMessage 列表）

    Returns:
        最近一次 search_web 的 queries 列表；从未调过 search_web 返回 None
    """
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                if tc.get("name") == "search_web":
                    args = tc.get("args") or {}
                    qs = args.get("queries")
                    if isinstance(qs, list):
                        return [str(q) for q in qs]
    return None


def queries_cover_missing(queries: list[str] | None, missing: list[int]) -> bool:
    """判断 search_web queries 是否覆盖全部缺失年份。

    Args:
        queries: 上一轮 search_web 的 queries（None 表示未调过）
        missing: 缺失年份列表

    Returns:
        True queries 非空且每个缺失年份至少出现在一个 query 的文本中
    """
    if not queries:
        return False
    for y in missing:
        if not any(str(y) in q for q in queries):
            return False
    return True
```

- [ ] **Step 3: 重写态 B missing 分支为决策化**

`src/agents/graph/verify/node.py` 态 B 的 missing 处理（Task 1 Step 7 内联保留段）替换为：

```python
    answer = state.answer or ""
    required = ctx.temporal_years if ctx is not None else []
    missing = completeness_check(required, answer) if required else []
    if missing:
        # ── 1. 询问/记住用户联网意愿 ──
        confirmed = False
        if ctx is not None and not ctx.web_confirmed:
            confirmed = await _ask_web_confirm(state, missing)
            if confirmed:
                ctx.web_confirmed = True
            else:
                covered = [y for y in required if y not in missing]
                answer = f"{answer}\n\n> 注：知识库仅覆盖 {covered}，缺失 {missing} 未联网补充。"
                return {"answer": answer, "_needs_regenerate": False}
        if ctx is None or not (confirmed or ctx.web_confirmed):
            covered = [y for y in required if y not in missing]
            answer = f"{answer}\n\n> 注：知识库仅覆盖 {covered}，缺失 {missing} 未联网补充。"
            return {"answer": answer, "_needs_regenerate": False}

        # ── 2. 决策化：看 agent 上一轮是否联网、queries 带全没有 ──
        last_queries = last_search_web_queries(state.messages)
        queries_covered = queries_cover_missing(last_queries, missing)
        if last_queries is not None and queries_covered:
            # 上一轮已一次带全缺失年份调 search_web，答案仍缺 → 网上真没有 → 直通
            logger.info(
                "verify regen stop (web exhausted) session_id={} missing={}",
                state.session_id,
                missing,
            )
            covered = [y for y in required if y not in missing]
            answer = f"{answer}\n\n> 注：知识库与网络均未覆盖 {missing}，仅 {covered} 有数据。"
            return {"answer": answer, "_needs_regenerate": False}

        # ── 3. 保险丝：修订次数达上限 → 标注直通（防 agent 反复不执行/带漏）──
        if state._verify_regenerations >= settings_max_verify_regenerations():
            covered = [y for y in required if y not in missing]
            answer = f"{answer}\n\n> 注：知识库与网络均未覆盖 {missing}，仅 {covered} 有数据。"
            return {"answer": answer, "_needs_regenerate": False}
        state._verify_regenerations += 1

        # ── 4. 注入/重申联网指引 → regen ──
        # 防重复注入：指引已在 messages 只重申不带全提示；否则注入完整指引
        from src.config.const import VERIFY_GUIDANCE_MARKER

        already_guided = any(
            isinstance(m, SystemMessage) and VERIFY_GUIDANCE_MARKER in (m.content or "")
            for m in state.messages
        )
        hint = (
            "（注意：search_web 支持一次传入多个查询，请一次带全以上所有缺失年份）"
            if last_queries is not None and not queries_covered
            else ""
        )
        guidance = SystemMessage(
            content=(
                f"知识库缺失年份 {missing}，{VERIFY_GUIDANCE_MARKER}，"
                f"请调用 search_web 工具补充这些年份的数据后再回答。{hint}"
            )
        )
        return {
            "answer": answer,
            "messages": [guidance] if not already_guided else [],
            "_needs_regenerate": True,
        }
```

其中 `settings_max_verify_regenerations()` 读 const（实现为 `from src.config.const import MAX_VERIFY_REGENERATIONS; return MAX_VERIFY_REGENERATIONS`，或直接 import 常量比较）。

**说明**：Task 3 结束时态 B 的 `already_guided` 分支不再"只置 regen 无刹车"——决策化已保证"带全仍缺"直通，保险丝兜住"agent 不执行/带漏"的极端场景。

- [ ] **Step 4: 态 A 引导换计数**

`verify/guardrails.py` 的 `web_citation_guard` 原条件 `state._agent_iterations < state._max_agent_iterations` 改为基于 `_verify_regenerations`（态 A 引导一次即直通，受保险丝约束即可）：

```python
def _web_citation_guard_enabled(state: AgentState) -> bool:
    """态 A 引导触发条件：已联网、无引用、未达保险丝上限、指引未注入过。"""
    return state._verify_regenerations < MAX_VERIFY_REGENERATIONS
```

（完整实现替换原 234 行的 `_agent_iterations < _max_agent_iterations` 条件为 `_verify_regenerations < MAX_VERIFY_REGENERATIONS`；注入成功后 `_verify_regenerations += 1`。）

- [ ] **Step 5: 新增图集成测试（决策化三场景）**

在 `tests/agents/graph/test_graph.py` 追加（复用现有 SequenceChatModel / fake_search_web / _run_graph）：

```python
@pytest.mark.asyncio
async def test_graph_verify_web_exhausted_annotates(monkeypatch):
    """联网带全缺失年份仍缺 → 标注直通不空转（决策化核心场景）。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "src.agents.graph.verify.faithfulness.faithfulness_check",
        AsyncMock(return_value=[]),
    )
    _ctx, token = _make_verify_ctx()
    try:
        llm = SequenceChatModel(
            [
                AIMessage(content="2024年营收3943亿"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_web",
                            "args": {"queries": ["腾讯2023年报", "腾讯2025年报"]},
                            "id": "w1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="2024年营收3943亿，2025年4500亿"),  # 仍缺 2023
            ]
        )
        graph = _build_test_graph(llm)
        initial = AgentState.make_initial_state("s1", "kb1", "这几年营收怎么样", [])
        node_order, last_verify, _ = await _run_graph(graph, initial)
        assert node_order[-1] == "format"
        assert last_verify["_needs_regenerate"] is False
        assert "知识库与网络均未覆盖" in last_verify["answer"]
        assert last_verify["_verify_regenerations"] == 1  # 保险丝未耗尽，决策化先停
    finally:
        current_request_ctx.reset(token)
```

（另加 queries 未带全→再试一次、未调 search_web→注入 regen 两场景，复用同结构。）

- [ ] **Step 6: 运行测试 + 全量回归**

Run: `pytest tests/agents/graph/test_verify_node.py tests/agents/graph/test_graph.py -v && pytest tests/ -v`
Expected: 新增用例过 + 全量过（既有 verify 循环测试若断言旧逻辑，同步更新）

- [ ] **Step 7: Commit**

```bash
git add src/agents/graph/state.py src/config/const.py src/agents/graph/verify/ tests/agents/graph/
git commit -m "fix: verify regen 决策化（看 agent 上一轮 search_web queries，带全仍缺直通；保险丝防死循环）"
```

---

### Task 4: C1-3 KB 输出护栏（态 B 校验器接入管道）

**Files:**
- Modify: `src/agents/graph/verify/guardrails.py`（加 kb_citation_guardrail）
- Modify: `src/agents/graph/verify/checks.py`（加 has_kb_context / answer_has_citation 辅助，若未在 Task 1 建）
- Modify: `src/agents/graph/verify/node.py`（态 B 走管道 [completeness 决策化, kb_guardrail, faithfulness]）
- Modify: `src/config/const.py`（加 KB citation marker）
- Test: `tests/agents/graph/test_verify_node.py`

**Interfaces:**
- Consumes: Task 3 决策化
- Produces: `kb_citation_guardrail(state, ctx) -> dict | None` 校验器；态 B 管道正式化

- [ ] **Step 1: const.py 加 KB citation marker**

```python
# verify KB 强制溯源指引的标记短语（态 B）：检索到 KB context 但答案无 [n] 时注入
VERIFY_KB_CITATION_MARKER: str = "请为知识库引用标注来源编号"
```

- [ ] **Step 2: guardrails.py 实现 kb_citation_guardrail**

```python
"""KB 强制溯源护栏（态 B）：检索到 KB context 但答案无 [n] 引用时引导重生成。"""

from langchain_core.messages import SystemMessage
from loguru import logger

from src.agents.graph.state import AgentState
from src.config.const import SSEInteractionTexts, VERIFY_KB_CITATION_MARKER
from src.infra.llm.request_context import RequestContext


def _has_kb_context(ctx) -> bool:
    """tool_contexts 是否含 kind=kb 的检索上下文。"""
    if ctx is None:
        return False
    return any(
        getattr(c, "kind", None) == SSEInteractionTexts.CITATION_KIND_KB
        for c in ctx.tool_contexts
    )


def _answer_has_citation(answer: str) -> bool:
    """回答是否含 [n] 引用标记。"""
    import re

    return re.search(r"\[\d+\]", answer) is not None


def _is_abstention_or_kb_uncovered(answer: str) -> bool:
    """回答是否拒答或表达"知识库未覆盖"（此时不强灌引用）。"""
    markers = ("未在文档中找到", "知识库未覆盖", "不在当前知识库范围")
    return any(m in answer for m in markers)


async def kb_citation_guardrail(
    state: AgentState, ctx: RequestContext | None
) -> dict | None:
    """KB 答案强制溯源：有 kb context 但答案无 [n] → 注入指引重生成。

    Args:
        state: 当前图状态
        ctx: 请求上下文

    Returns:
        None 通过（有引用/拒答/未覆盖/无 kb context）；
        注入指引的 regen 决策 dict（有 kb context 且无 [n] 且非拒答）
    """
    answer = state.answer or ""
    if (
        not _has_kb_context(ctx)
        or _answer_has_citation(answer)
        or _is_abstention_or_kb_uncovered(answer)
    ):
        return None
    already_guided = any(
        isinstance(m, SystemMessage)
        and VERIFY_KB_CITATION_MARKER in (m.content or "")
        for m in state.messages
    )
    if already_guided:
        # 已引导过仍无引用：不强灌第二次（避免与模型"判断无关"冲突）
        return None
    logger.info(
        "verify kb-citation guide session_id={} answer_len={}",
        state.session_id,
        len(answer),
    )
    guidance = SystemMessage(
        content=(
            f"你刚才的回答基于知识库检索结果，但没有标注来源编号，"
            f"{VERIFY_KB_CITATION_MARKER}，请在引用来源的对应句末补上 [n] 编号"
            "（编号须与检索返回的来源列表一致）后重新回答。"
        )
    )
    return {"answer": answer, "messages": [guidance], "_needs_regenerate": True}
```

- [ ] **Step 3: node.py 态 B 走完整管道**

态 B 分支收尾重构为管道（completeness 决策化结果先行；KB 护栏在完整性通过后、judge 前）：

```python
        # ── 完整性决策化通过后（无 missing）── 走 KB 护栏 + judge
        if not missing:
            # KB 溯源护栏（在 judge 前）
            guardrail = await kb_citation_guardrail(state, ctx)
            if guardrail is not None:
                return guardrail
            # 忠实度 judge
            contexts = ctx.tool_contexts if ctx is not None else []
            unsupported = await faithfulness_check(answer, contexts)
            if unsupported:
                return {
                    "answer": answer,
                    "_unsupported": unsupported,
                    "_needs_regenerate": False,
                }
            return {"answer": answer, "_needs_regenerate": False}
        # missing 非空 → 走 Task 3 决策化逻辑（在上面 return）
```

**注意**：Task 4 完成后态 B 控制流为——missing 有值 → 决策化（联网询问/决策 regen/直通）；missing 空 → kb 护栏 → judge → 直通。两态两管道结构最终成形。若 node.py 超 80 行，把态 B missing 决策化抽到 `verify/regen_decision.py` 独立函数。

- [ ] **Step 4: 新增护栏单测**

`tests/agents/graph/test_verify_node.py` 追加：

```python
def _make_kb_ctx_contexts():
    """构造含 kind=kb 的引用上下文。"""
    return [RAGContext(content="内容", source="a.pdf", page=1, doc_id="d1", chunk_id="d1:0", kind="kb")]


@pytest.mark.asyncio
async def test_kb_guardrail_guides_when_no_citation():
    """态 B 有 kb context 无 [n] → 注入指引 regen。"""
    from src.agents.graph.verify.guardrails import kb_citation_guardrail

    state = AgentState(answer="腾讯2024年营收3943亿")
    ctx = RequestContext(session_id="s1", tool_contexts=_make_kb_ctx_contexts())
    decision = await kb_citation_guardrail(state, ctx)
    assert decision is not None
    assert decision["_needs_regenerate"] is True
    assert VERIFY_KB_CITATION_MARKER in decision["messages"][0].content


@pytest.mark.asyncio
async def test_kb_guardrail_skips_when_abstention():
    """拒答/知识库未覆盖 → 不强灌引用。"""
    from src.agents.graph.verify.guardrails import kb_citation_guardrail

    state = AgentState(answer="未在文档中找到相关数据")
    ctx = RequestContext(session_id="s1", tool_contexts=_make_kb_ctx_contexts())
    assert await kb_citation_guardrail(state, ctx) is None


@pytest.mark.asyncio
async def test_kb_guardrail_passes_when_cited():
    """答案带 [n] → 直接通过。"""
    from src.agents.graph.verify.guardrails import kb_citation_guardrail

    state = AgentState(answer="腾讯2024年营收3943亿[1]")
    ctx = RequestContext(session_id="s1", tool_contexts=_make_kb_ctx_contexts())
    assert await kb_citation_guardrail(state, ctx) is None
```

（import 处补 `VERIFY_KB_CITATION_MARKER`。）

- [ ] **Step 5: 全量回归**

Run: `pytest tests/ -v && ruff check src/agents/graph/verify/ && pyright src/agents/graph/verify/`
Expected: 全过 / 无 error

- [ ] **Step 6: Commit**

```bash
git add src/agents/graph/verify/ src/config/const.py tests/agents/graph/test_verify_node.py
git commit -m "feat: KB 输出护栏（态 B 有 kb context 无 [n] → 引导补标，含拒答/未覆盖排除）"
```

---

### Task 5: 收尾质量门禁

**Files:**
- Modify: `docs/openspec/changes/agent-loop-hardening/tasks.md`（勾选全部）

**Interfaces:**
- Consumes: Task 1-4 产物

- [ ] **Step 1: 全量回归 + 门禁**

Run: `pytest tests/ -v && ruff check . && pyright src/`
Expected: 全过 / 无 error / 不新增 pyright error

- [ ] **Step 2: 契约同步检查**

Run: `grep -rn "_resolved_kb_ids" src/ tests/ | grep -v __pycache__`
Expected: 无输出（字段已完全退役）

- [ ] **Step 3: 手动验证三场景**

- 绑定 KB，答案无 [n] → 日志出现 `verify kb-citation guide`，重生成带 [n]
- 未绑定 KB 纯对话 → verify 走态 A 管道，不跑 judge
- 绑 KB "这几年业绩"，KB 缺年份 → 联网（queries 带全）仍缺 → 标注"知识库与网络均未覆盖"

- [ ] **Step 4: 勾选 change tasks + Commit**

修改 `docs/openspec/changes/agent-loop-hardening/tasks.md` 全勾，然后：

```bash
git add docs/openspec/changes/agent-loop-hardening/
git commit -m "docs: agent-loop-hardening tasks 全部完成"
```
