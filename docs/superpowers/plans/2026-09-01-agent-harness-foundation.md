# agent-harness-foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将项目从"Corporate RAG 问答系统"升级为"Corporate Agent Harness 底座"：时间结构化约束修复"这几年只答 2024"、答案验证循环、工具注册表化（KB=RAG 开关）、聊天页 UI 重构。

**Architecture:** 保持现有 LangGraph 图（kb_router → agent → tools → agent_finalize → format）不动 kb_router；时间解析在 retrieve_kb 工具内接入（正则粗筛触发），验证循环在 agent_finalize → format 之间插入 verify 节点；make_rag_tools 升级为 ToolRegistry；chat.html 按设计稿重构。

**Tech Stack:** Python 3.11+ / FastAPI / LangGraph / LangChain / ChromaDB / DashScope / pytest / playwright-cli

**Spec 来源:** `docs/openspec/changes/agent-harness-foundation/`（proposal/design/specs/tasks）；UI 设计稿 `docs/design/pages/chat-harness.md` + `docs/design/chat-harness-mockup.html`

## Global Constraints

- 不用三元表达式（`a if cond else b`），写完整 if/else
- 类型不确定的值用显式 `isinstance`/`x is not None`，不用 `getattr(x, "attr", default)` 兜底
- 新增常量/阈值/文案集中 `src/config/`（settings.py 可配项 / const.py 固定值 / prompts.py 提示词）
- 所有函数写 docstring；dataclass 字段加行内注释；文件超 400 行拆模块
- 测试 mock 外部依赖，不发起真实网络调用
- 生产单 worker（进程内状态）
- 契约改动同步 `docs/agents/api_contract.md` 与受影响测试断言

## 文件结构

**新增：**
- `src/rag/temporal.py` — 时间解析（候选派生/正则粗筛/LLM 解析/缺失判定）
- `src/agents/graph/verify_node.py` — 验证循环节点（完整性/忠实度/联网询问）
- `src/agents/tools/registry.py` — ToolRegistry 注册表
- `tests/rag/test_temporal.py`、`tests/agents/graph/test_verify_node.py`、`tests/agents/tools/test_registry.py`

**修改：**
- `src/infra/llm/request_context.py` — 扩展 temporal_years/missing_years/web_confirmed
- `src/agents/tools/rag_tools.py` — retrieve_kb 接入时间解析、工具注册表化、KB 开关
- `src/agents/tools/web_tools.py` — search_web 升级 queries 数组
- `src/agents/graph/workflow.py` — 插入 verify 节点
- `src/agents/graph/agent_node.py` — 无
- `src/agents/graph/nodes.py` — kb_router_node 空值语义（不检索）
- `src/config/prompts.py` — 忠实报告准则（纯对话轻量自检）
- `src/config/settings.py` — TEMPORAL_PARSE_ENABLED / VERIFY_ENABLED 开关
- `src/config/const.py` — verify 询问独立计数常量
- `deploy/nginx/html/chat.html` — UI 重构（frontend-design）
- `docs/agents/api_contract.md` / `docs/agents/glossary.md` — kb_id 空串语义
- `CLAUDE.md` — 认知层调整

---

## Part A: CLAUDE.md 认知层

### Task A1: CLAUDE.md 定位调整

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 改标题与角色**

将 `# Corporate RAG` 改为 `# Corporate Agent Harness`；角色描述改为：

```
你是资深 Python 后端与 AI 应用架构师，平常习惯是用中文，文档，注释都是用中文的，
负责企业智能助手 harness（聊天底座 + 可插拔工具/知识库）的设计、实现与优化；RAG 知识库是其中一个能力模块。
```

- [ ] **Step 2: 技术栈补 MCP**

技术栈行加 `/ MCP`。

- [ ] **Step 3: 参考项目优先级**

`docs/agents/reference-projects.md` 中 claude-code / deepseek-harness / codex 的"何时查阅"标注"设计 agent 混合编排/横切环节/工具系统时优先参考"。

- [ ] **Step 4: 提交**

```bash
git add CLAUDE.md docs/agents/reference-projects.md
git commit -m "docs: CLAUDE.md 认知层转 Corporate Agent Harness"
```

---

## Part B: 时间结构化约束（spec: temporal-constraint）

### Task B1: RequestContext 扩展

**Files:**
- Modify: `src/infra/llm/request_context.py`
- Test: `tests/infra/llm/test_request_context.py`

**Interfaces:**
- Produces: `RequestContext.temporal_years: list[int]`、`RequestContext.missing_years: list[int]`、`RequestContext.web_confirmed: bool`

- [ ] **Step 1: 写失败测试**

```python
def test_request_context_temporal_fields_defaults():
    ctx = RequestContext(session_id="s1")
    assert ctx.temporal_years == []
    assert ctx.missing_years == []
    assert ctx.web_confirmed is False
```

- [ ] **Step 2: 跑失败**

Run: `pytest tests/infra/llm/test_request_context.py -v`
Expected: FAIL（无 temporal_years 属性）

- [ ] **Step 3: 实现**

在 `RequestContext` dataclass 末尾追加字段：

```python
    temporal_years: list[int] = field(
        default_factory=list
    )  # 时间解析出的要求覆盖年份（来源：时间解析层；用途：验证循环完整性验收标准）
    missing_years: list[int] = field(
        default_factory=list
    )  # 知识库缺失年份（来源：时间解析比对；用途：询问用户是否联网的依据）
    web_confirmed: bool = (
        False  # 会话内已确认联网（来源：验证循环询问用户后置位；用途：后续缺失年份不再询问）
    )
```

- [ ] **Step 4: 跑通过**

Run: `pytest tests/infra/llm/test_request_context.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/infra/llm/request_context.py tests/infra/llm/test_request_context.py
git commit -m "feat: RequestContext 扩展 temporal/missing_years/web_confirmed"
```

### Task B2: 候选年份派生 + 时间词粗筛 + 缺失判定

**Files:**
- Create: `src/rag/temporal.py`
- Test: `tests/rag/test_temporal.py`

**Interfaces:**
- Produces:
  - `derive_candidate_years(kb_ids: list[str]) -> list[int]` — 从 KB 文档 meta_info 聚合 year/report_period
  - `has_temporal_words(query: str) -> bool` — 正则粗筛（`近|这|上|今|去|几|最近|前几年`）
  - `compute_missing(years: list[int], covered: list[int]) -> list[int]`

- [ ] **Step 1: 写失败测试**

```python
import pytest
from src.rag.temporal import has_temporal_words, compute_missing


def test_has_temporal_words_hit():
    assert has_temporal_words("腾讯这几年业绩怎么样") is True
    assert has_temporal_words("近三年营收") is True


def test_has_temporal_words_miss():
    assert has_temporal_words("腾讯 2024 年营收多少") is False
    assert has_temporal_words("腾讯营收构成") is False


def test_compute_missing():
    assert compute_missing([2023, 2024, 2025], [2024]) == [2023, 2025]
    assert compute_missing([2024], [2024]) == []
```

- [ ] **Step 2: 跑失败**

Run: `pytest tests/rag/test_temporal.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```python
"""时间解析模块 — 候选年份派生、时间词粗筛、缺失判定。

供 retrieve_kb 工具内部调用；时间解析结果写入 RequestContext 供验证循环读取。
"""
import re

# 时间词粗筛（包含判断，不要求精确匹配新变体）
_TEMPORAL_WORD_PATTERN = re.compile(r"近|这|上|今|去|几|最近|前几年")


def has_temporal_words(query: str) -> bool:
    """判断查询是否含相对时间词（正则粗筛，命中才调 LLM 解析）。

    Args:
        query: 用户查询文本

    Returns:
        True 表示含相对时间词，需要进入 LLM 时间解析
    """
    return _TEMPORAL_WORD_PATTERN.search(query) is not None


def compute_missing(years: list[int], covered: list[int]) -> list[int]:
    """计算要求年份中未被知识库覆盖的年份。

    Args:
        years: 解析出的要求覆盖年份列表
        covered: 知识库实际覆盖年份列表

    Returns:
        缺失年份列表（升序）
    """
    cover_set = set(covered)
    missing = [y for y in years if y not in cover_set]
    return sorted(missing)


async def derive_candidate_years(kb_ids: list[str]) -> list[int]:
    """从绑定 KB 文档元数据聚合候选年份。

    读取 KB 文档 meta_info 的 year / report_period 实体，聚合去重排序；
    KB 为空或元数据缺失时返回空列表（调用方走"无时间约束"路径）。

    Args:
        kb_ids: 知识库 ID 列表

    Returns:
        候选年份列表（升序，可能为空）
    """
    from src.infra.db.engine import session_factory
    from src.infra.db.mysql_db.document_repo import DocumentRepo

    years: set[int] = set()
    repo = DocumentRepo(session_factory)
    for kb_id in kb_ids:
        docs = await repo.get_documents(kb_id)
        for doc in docs:
            if not doc.meta_info:
                continue
            try:
                import json
                meta = json.loads(doc.meta_info)
            except (json.JSONDecodeError, TypeError):
                continue
            entities = meta.get("entities") or {}
            year = entities.get("year")
            if year:
                try:
                    years.add(int(year))
                except (TypeError, ValueError):
                    pass
            period = entities.get("report_period") or ""
            if isinstance(period, str):
                m = re.search(r"(20\d{2})", period)
                if m:
                    years.add(int(m.group(1)))
    return sorted(years)
```

- [ ] **Step 4: 跑通过**

Run: `pytest tests/rag/test_temporal.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/rag/temporal.py tests/rag/test_temporal.py
git commit -m "feat: 时间解析模块（候选年份派生/时间词粗筛/缺失判定）"
```

### Task B3: LLM 时间解析器

**Files:**
- Modify: `src/rag/temporal.py`
- Test: `tests/rag/test_temporal.py`

**Interfaces:**
- Produces: `parse_temporal(query: str, candidates: list[int], llm) -> dict` — 返回 `{"years": list[int], "has_temporal": bool}`；LLM 失败回退最近 3 年（含当年往前）∩ 候选

- [ ] **Step 1: 写失败测试**

```python
from src.rag.temporal import parse_temporal


def test_parse_temporal_candidates_only(monkeypatch):
    class FakeLLM:
        async def ainvoke(self, messages, **kwargs):
            return type("R", (), {"content": '{"years": [2023, 2024, 2025]}'})()

    result = parse_temporal("这几年", [2022, 2023, 2024, 2025], FakeLLM())
    assert result["years"] == [2023, 2024, 2025]
    assert result["has_temporal"] is True


def test_parse_temporal_out_of_candidate_rejected(monkeypatch):
    class FakeLLM:
        async def ainvoke(self, messages, **kwargs):
            return type("R", (), {"content": '{"years": [2019, 2024]}'})()

    result = parse_temporal("这几年", [2022, 2023, 2024, 2025], FakeLLM())
    assert 2019 not in result["years"]


def test_parse_temporal_fallback(monkeypatch):
    class FakeLLM:
        async def ainvoke(self, messages, **kwargs):
            raise RuntimeError("llm down")

    result = parse_temporal("这几年", [], FakeLLM())
    assert result["has_temporal"] is False
```

- [ ] **Step 2: 跑失败**

Run: `pytest tests/rag/test_temporal.py::test_parse_temporal -v`
Expected: FAIL（parse_temporal 未定义）

- [ ] **Step 3: 实现**（追加到 temporal.py）

```python
import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

_BEIJING_TZ = ZoneInfo("Asia/Shanghai")
_FALLBACK_N_YEARS = 3


def _fallback_recent_years(candidates: list[int]) -> list[int]:
    """LLM 失败时回退：最近 N 个完整年度 ∩ 候选。"""
    this_year = datetime.now(_BEIJING_TZ).year
    recent = [this_year - i for i in range(1, _FALLBACK_N_YEARS + 1)]
    cand = set(candidates)
    return [y for y in recent if y in cand]


def parse_temporal(query: str, candidates: list[int], llm) -> dict:
    """LLM 解析相对时间词为候选集合内的年份（含代码校验）。

    Args:
        query: 含相对时间词的查询
        candidates: 候选年份集合（KB 元数据派生，可为空）
        llm: ChatOpenAI 实例（flash）

    Returns:
        {"years": [...], "has_temporal": bool}；LLM 失败/输出越界时回退最近 3 年 ∩ 候选
    """
    if not candidates:
        return {"years": [], "has_temporal": False}
    today = datetime.now(_BEIJING_TZ).date()
    prompt = (
        "你是时间解析器。把用户查询中的相对时间词解析为具体年份，"
        f"只能从候选集合中选择，不得输出候选之外的年份。\n"
        f"今天是 {today.year}年{today.month}月{today.day}日。\n"
        f"候选年份: {candidates}\n查询: {query}\n"
        '输出 JSON: {"years": [年份列表]}（完整年度，排除进行中的当年）'
    )
    from langchain_core.messages import HumanMessage

    try:
        resp = asyncio.get_event_loop().run_until_complete(
            llm.ainvoke([HumanMessage(content=prompt)], temperature=0)
        )
        raw = (getattr(resp, "content", None) or "").strip()
        data = json.loads(raw)
        years = [int(y) for y in data.get("years", []) if isinstance(y, (int, str))]
    except Exception:
        return {"years": _fallback_recent_years(candidates), "has_temporal": True}
    cand = set(candidates)
    valid = [y for y in years if y in cand]
    if not valid:
        return {"years": _fallback_recent_years(candidates), "has_temporal": True}
    return {"years": sorted(valid), "has_temporal": True}
```

> 注：`parse_temporal` 目前是同步签名（内部 run_until_complete 适配工具调用场景）。若后续接入事件循环已有环境，改为 `async def parse_temporal` 并在调用方 await。

- [ ] **Step 4: 跑通过**

Run: `pytest tests/rag/test_temporal.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/rag/temporal.py tests/rag/test_temporal.py
git commit -m "feat: LLM 时间解析器（候选内选择 + 越界拒绝 + 失败回退）"
```

### Task B4: retrieve_kb 接入时间解析 + 配置开关

**Files:**
- Modify: `src/agents/tools/rag_tools.py`
- Modify: `src/config/settings.py`
- Test: `tests/agents/tools/test_rag_tools.py`

**Interfaces:**
- Consumes: `has_temporal_words` / `parse_temporal` / `derive_candidate_years` / `compute_missing`（B2/B3）
- Consumes: `settings.TEMPORAL_PARSE_ENABLED`

- [ ] **Step 1: 加配置开关（settings.py）**

```python
# 时间结构化约束开关：关闭时跳过时间解析（出错可即时关闭）
TEMPORAL_PARSE_ENABLED: bool = os.getenv("TEMPORAL_PARSE_ENABLED", "true").lower() in (
    "true", "1", "yes"
)
```

- [ ] **Step 2: 写失败测试**

```python
from src.agents.tools import rag_tools


def test_retrieve_kb_writes_temporal_years(monkeypatch):
    # 构造 retrieve_kb 工具（依赖 mock），调用后断言 ctx.temporal_years / missing_years
    ...
```

（实现细节依赖 mock vector_store/reranker，见现有 test_rag_tools.py 模式；断言 `current_request_ctx.get().missing_years` 在含时间词查询后正确填充，未含时间词查询为 `[]`。）

- [ ] **Step 3: 实现**（rag_tools.py `retrieve_kb` 开头，检索前）

```python
from src.rag.temporal import (
    compute_missing,
    derive_candidate_years,
    has_temporal_words,
    parse_temporal,
)

        # 时间结构化约束（grilling 决策）：正则粗筛命中才解析；结果写入 RequestContext
        ctx = current_request_ctx.get()
        if settings.TEMPORAL_PARSE_ENABLED and ctx is not None and kb_ids:
            if has_temporal_words(query):
                candidates = await derive_candidate_years(kb_ids)
                parsed = await parse_temporal_async(query, candidates, llm_for_parse)
                ctx.temporal_years = parsed["years"]
                ctx.missing_years = compute_missing(parsed["years"], candidates)
```

> 注：若 B3 保持同步签名，此处 `parse_temporal_async` 为 `asyncio.to_thread(parse_temporal, ...)` 的包装；llm_for_parse 用 `get_classify_llm()`（flash）。

- [ ] **Step 4: 跑通过 + 开关测试**

Run: `pytest tests/agents/tools/test_rag_tools.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/tools/rag_tools.py src/config/settings.py tests/agents/tools/test_rag_tools.py
git commit -m "feat: retrieve_kb 接入时间解析（正则粗筛触发 + 配置开关）"
```

---

## Part C: 验证循环（spec: answer-verification）

### Task C1: 完整性校验 + 忠实度 judge

**Files:**
- Create: `src/agents/graph/verify_node.py`
- Test: `tests/agents/graph/test_verify_node.py`

**Interfaces:**
- Produces:
  - `extract_years(answer: str) -> set[int]` — 正则提取答案 4 位年份
  - `completeness_check(required: list[int], answer: str) -> list[int]` — 缺失年份
  - `faithfulness_check(answer: str, contexts: list) -> list[str]` — 无支撑句子（RAGAS_LLM_MODEL judge）

- [ ] **Step 1: 写失败测试**

```python
from src.agents.graph.verify_node import extract_years, completeness_check


def test_extract_years():
    assert extract_years("2024年营收 3943 亿，2023 年 3000 亿") == {2023, 2024}
    assert extract_years("近三年持续增长") == set()


def test_completeness_check():
    assert completeness_check([2023, 2024, 2025], "2024年营收3943亿") == [2023, 2025]
    assert completeness_check([2024], "2024年营收3943亿") == []
```

- [ ] **Step 2: 跑失败**

Run: `pytest tests/agents/graph/test_verify_node.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```python
"""验证循环节点 — 完整性/忠实度校验 + 缺失联网询问。

挂在 agent_finalize → format 之间；仅在会话绑定 KB 时生效（纯对话跳过）。
"""
import re

_YEAR_PATTERN = re.compile(r"20\d{2}")


def extract_years(answer: str) -> set[int]:
    """正则提取答案文本中的 4 位年份。

    Args:
        answer: 答案文本（AgentState.answer）

    Returns:
        年份集合（可能为空）
    """
    return {int(m) for m in _YEAR_PATTERN.findall(answer)}


def completeness_check(required: list[int], answer: str) -> list[int]:
    """比对要求覆盖年份与答案实际覆盖年份，返回缺失。

    Args:
        required: 问题要求覆盖年份（RequestContext.temporal_years）
        answer: 答案文本

    Returns:
        缺失年份列表（升序）
    """
    covered = extract_years(answer)
    missing = [y for y in required if y not in covered]
    return sorted(missing)


async def faithfulness_check(answer: str, contexts: list) -> list[str]:
    """用 RAGAS_LLM_MODEL judge 核对答案事实点是否被引用上下文支撑。

    Args:
        answer: 答案文本
        contexts: 引用上下文（RequestContext.tool_contexts 的 content 列表）

    Returns:
        无支撑句子清单（judge 只标记，不删内容）
    """
    if not contexts:
        return []
    from src.config import settings
    from src.models import get_classify_llm

    llm = get_classify_llm()  # 评估专用模型（RAGAS_LLM_MODEL，temperature 0）
    evidence = "\n".join(c.content if hasattr(c, "content") else str(c) for c in contexts)[:8000]
    prompt = (
        "检查回答中的每个事实点是否被引用证据支撑。\n"
        f"引用证据:\n{evidence}\n回答:\n{answer}\n"
        '输出无支撑句子清单（JSON {"unsupported": ["句子1", ...]}，全部有支撑则空数组）'
    )
    from langchain_core.messages import HumanMessage

    try:
        resp = await llm.ainvoke([HumanMessage(content=prompt)], temperature=0)
        raw = (getattr(resp, "content", None) or "").strip()
        import json
        data = json.loads(raw)
        return [s for s in data.get("unsupported", []) if s.strip()]
    except Exception:
        return []
```

- [ ] **Step 4: 跑通过**

Run: `pytest tests/agents/graph/test_verify_node.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/verify_node.py tests/agents/graph/test_verify_node.py
git commit -m "feat: 完整性校验 + 忠实度 judge（verify_node）"
```

### Task C2: verify 节点 + 缺失联网询问 + workflow 接入

**Files:**
- Modify: `src/agents/graph/verify_node.py`
- Modify: `src/agents/graph/workflow.py`
- Modify: `src/agents/tools/ask_tools.py`（提取可复用投递函数，若需要）
- Modify: `src/config/const.py` / `settings.py`（VERIFY_ENABLED / 独立计数）
- Test: `tests/agents/graph/test_verify_node.py`、`tests/services/test_dual_stream.py`（适配）

**Interfaces:**
- Consumes: `completeness_check` / `faithfulness_check`（C1）、`current_request_ctx`、`clarify_channel`
- Produces: `verify_node(state: AgentState) -> dict`（返回 `answer`/`citations` 或触发询问信号）

- [ ] **Step 1: 加配置开关（settings.py）**

```python
# 验证循环开关：关闭时 verify 节点直通（出错可即时关闭）
VERIFY_ENABLED: bool = os.getenv("VERIFY_ENABLED", "true").lower() in ("true", "1", "yes")
```

- [ ] **Step 2: 实现 verify_node**

```python
"""verify_node 主体：未绑定 KB → 直通；完整性缺失 → 询问是否联网；最终答案跑 judge。"""
import asyncio

from src.agents.graph.state import AgentState
from src.agents.graph.verify_node import completeness_check, faithfulness_check
from src.config import settings
from src.infra.llm.request_context import current_request_ctx


def _ask_web_confirm(session_id: str, missing_years: list[int]) -> bool:
    """经 clarify_channel 询问用户是否联网，返回用户确认结果（阻塞等待，独立计数）。

    Args:
        session_id: 会话 ID
        missing_years: 缺失年份列表

    Returns:
        True 用户确认联网；False 拒绝或超时
    """
    import asyncio
    from src.config.const import ASK_USER_TIMEOUT
    from src.infra.llm.request_context import pending_asks

    ctx = current_request_ctx.get()
    if ctx is None:
        return False
    question_id = "web_confirm"
    payload = {
        "type": "ask_user",
        "questions": [{
            "id": question_id,
            "question": f"知识库仅覆盖部分年份，缺失 {missing_years}，是否需要联网搜索补充？",
            "dimension": "free",
            "options": ["需要", "不需要"],
            "multi_select": False,
        }],
    }
    fut = asyncio.get_event_loop().create_future()
    pending_asks[session_id] = fut
    ctx.clarify_channel.put_nowait(payload)
    try:
        answers = asyncio.get_event_loop().run_until_complete(
            asyncio.wait_for(fut, timeout=ASK_USER_TIMEOUT)
        )
    except Exception:
        return False
    finally:
        pending_asks.pop(session_id, None)
    selected = answers[0].get("selected") if answers else ""
    return selected in ("需要", "需要联网")


async def verify_node(state: AgentState) -> dict:
    """验证循环节点：未绑定 KB 直通；完整性缺失询问；最终答案跑 judge。

    Args:
        state: 当前图状态

    Returns:
        {"answer": 答案}；缺失且需联网时，返回信号由条件边回 agent 重生成
    """
    if not settings.VERIFY_ENABLED:
        return {"answer": state.answer or ""}
    ctx = current_request_ctx.get()
    kb_bound = bool(getattr(state, "_resolved_kb_ids", None))
    if not kb_bound:
        # 纯对话：跳过 verify（claude-code 式轻量自检由 prompt 准则覆盖）
        return {"answer": state.answer or ""}

    answer = state.answer or ""
    required = ctx.temporal_years if ctx is not None else []
    missing = completeness_check(required, answer) if required else []
    if missing:
        if ctx is not None and not ctx.web_confirmed:
            confirmed = _ask_web_confirm(state.session_id, missing)
            if confirmed:
                ctx.web_confirmed = True
                # 回 agent 重生成（LLM 据缺失年份调 search_web 补充）
                return {"answer": answer, "_needs_regenerate": True}
            # 用户拒绝：标注缺失后直通
            answer = f"{answer}\n\n> 注：知识库仅覆盖 {required} 中的 {set(required) - set(missing)}，缺失 {missing} 未联网补充。"
        elif ctx is not None and ctx.web_confirmed:
            return {"answer": answer, "_needs_regenerate": True}
    # 最终答案跑忠实度 judge
    contexts = ctx.tool_contexts if ctx is not None else []
    unsupported = await faithfulness_check(answer, contexts)
    if unsupported:
        return {"answer": answer, "_unsupported": unsupported}
    return {"answer": answer}
```

- [ ] **Step 3: workflow.py 插入 verify 节点**

```python
from src.agents.graph.verify_node import verify_node

    builder.add_node("verify", verify_node)
    # agent_finalize → verify → (通过→format / 需重生成→agent)
    builder.add_edge("agent_finalize", "verify")
    builder.add_conditional_edges(
        "verify",
        lambda state: "agent" if getattr(state, "_needs_regenerate", False) else "format",
        {"agent": "agent", "format": LangGraphNode.Format.NAME},
    )
```

> 注：`_needs_regenerate` 需加进 `AgentState`（dataclass 字段，默认 False）；重生成时 `_agent_iterations` 上限兜底防死循环。

- [ ] **Step 4: 跑测试 + 适配存量**

Run: `pytest tests/agents/graph/ tests/services/ -v`
Expected: PASS（存量 dual_stream 测试若断言节点路径，适配）

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/verify_node.py src/agents/graph/workflow.py src/agents/graph/state.py src/config/settings.py
git commit -m "feat: verify 节点接入 workflow（缺失询问/回 agent 重生成/开关）"
```

### Task C3: 纯对话轻量自检 prompt 准则

**Files:**
- Modify: `src/config/prompts.py`

- [ ] **Step 1: FINANCIAL_SYSTEM_PROMPT 增加忠实报告准则**

在"回答规则"追加：

```
12. 回答必须忠实：不确定或无法验证的内容如实说明，不得编造；可联网核实的信息先联网核实；
    不得把未查证的信息描述为已查证
```

- [ ] **Step 2: 提交**

```bash
git add src/config/prompts.py
git commit -m "feat: 纯对话轻量自检 prompt 准则（claude-code 忠实报告模式）"
```

---

## Part D: 工具注册表化（spec: tool-registry）

### Task D1: ToolRegistry 实现

**Files:**
- Create: `src/agents/tools/registry.py`
- Test: `tests/agents/tools/test_registry.py`

**Interfaces:**
- Produces: `ToolRegistry` 类（`register(name, fn, deps=None, enabled=True)` / `unregister(name)` / `set_enabled(name, bool)` / `enabled_tools() -> list` / `get(name)`）

- [ ] **Step 1: 写失败测试**

```python
from src.agents.tools.registry import ToolRegistry


def test_register_and_enabled():
    reg = ToolRegistry()
    reg.register("a", lambda: 1)
    reg.register("b", lambda: 2, enabled=False)
    assert len(reg.enabled_tools()) == 1


def test_toggle_enable():
    reg = ToolRegistry()
    reg.register("a", lambda: 1)
    reg.set_enabled("a", False)
    assert reg.enabled_tools() == []
    reg.set_enabled("a", True)
    assert len(reg.enabled_tools()) == 1


def test_get_missing_raises():
    reg = ToolRegistry()
    try:
        reg.get("nope")
    except KeyError:
        pass
    else:
        raise AssertionError("should raise KeyError")
```

- [ ] **Step 2: 跑失败**

Run: `pytest tests/agents/tools/test_registry.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```python
"""工具注册表 — 可插拔工具管理（注册/启停/依赖注入）。

为 MCP 接入与未来 subagent 工具预留统一入口；agent 循环只消费 enabled_tools()。
"""
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolEntry:
    """注册表条目。

    fn: 可调用工具（LangChain tool 或装饰器产物）
    deps: 依赖注入 dict（工具闭包需要的外部依赖）
    enabled: 是否启用（停用不出现在 LLM 可见列表）
    """

    fn: Callable
    deps: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


class ToolRegistry:
    """按工具名管理注册条目，返回当前启用工具列表。"""

    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}

    def register(
        self, name: str, fn: Callable, deps: dict | None = None, enabled: bool = True
    ) -> None:
        """注册一个工具。

        Args:
            name: 工具名（LLM 可见）
            fn: 工具可调用对象
            deps: 依赖注入 dict
            enabled: 初始是否启用
        """
        self._entries[name] = ToolEntry(fn=fn, deps=deps or {}, enabled=enabled)

    def unregister(self, name: str) -> None:
        """注销工具（不存在时静默）。"""
        self._entries.pop(name, None)

    def set_enabled(self, name: str, enabled: bool) -> None:
        """按工具粒度启停。"""
        if name in self._entries:
            self._entries[name].enabled = enabled

    def enabled_tools(self) -> list[Callable]:
        """返回当前启用工具的可调用列表（供 LLM bind_tools）。"""
        return [e.fn for e in self._entries.values() if e.enabled]

    def get(self, name: str) -> Callable:
        """按名取工具（不存在抛 KeyError）。"""
        return self._entries[name].fn
```

- [ ] **Step 4: 跑通过**

Run: `pytest tests/agents/tools/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/tools/registry.py tests/agents/tools/test_registry.py
git commit -m "feat: ToolRegistry 注册表（注册/启停/依赖注入）"
```

### Task D2: make_rag_tools 改造 + KB=RAG 开关

**Files:**
- Modify: `src/agents/tools/rag_tools.py`
- Test: `tests/agents/tools/test_rag_tools.py`

**Interfaces:**
- Consumes: `ToolRegistry`（D1）
- Produces: `make_rag_tools(vector_store, bm25, reranker, prompt_manager, embed_fn, kb_bound: bool = True) -> list` — 未绑定 KB 时不注册 retrieve_kb

- [ ] **Step 1: 改造 make_rag_tools**

```python
def make_rag_tools(
    vector_store, bm25, reranker, prompt_manager, embed_fn, kb_bound: bool = True
) -> list:
    """构建工具列表：注册表管理；kb_bound=False（纯对话）时不注册 retrieve_kb。

    Args:
        ...（原有）
        kb_bound: 会话是否绑定知识库（未绑定=纯对话，不提供 RAG 检索工具）
    """
    from src.agents.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("retrieve_kb", retrieve_kb, deps={...}, enabled=kb_bound)
    registry.register("ask_user", ask_user)
    if settings.WEB_SEARCH_ENABLED:
        from src.agents.tools.web_tools import search_web
        registry.register("search_web", search_web)
    return registry.enabled_tools()
```

- [ ] **Step 2: 接入 kb_bound**

在 `workflow.py` 构建图时传 `kb_bound=bool(initial_state.kb_id)`；agent 循环前确定（通过 state.kb_id）。

- [ ] **Step 3: kb_router_node 空值语义改"不检索"（nodes.py）**

```python
        # kb_id 为空 = 未绑定 KB（纯对话），不检索（废弃隐式跨库）
        if not state.kb_id:
            return {"_resolved_kb_ids": []}
```

- [ ] **Step 4: 跑测试**

Run: `pytest tests/agents/ tests/api/test_chat.py -v`
Expected: PASS（存量空 kb_id 用例若走跨库断言，适配为"不检索"）

- [ ] **Step 5: 提交**

```bash
git add src/agents/tools/rag_tools.py src/agents/graph/workflow.py src/agents/graph/nodes.py
git commit -m "feat: 工具注册表化 + KB=RAG 开关（未绑定不注册 retrieve_kb，空 kb_id 不检索）"
```

### Task D3: search_web 升级 queries 数组

**Files:**
- Modify: `src/agents/tools/web_tools.py`
- Test: `tests/agents/tools/test_web_tools.py`

**Interfaces:**
- Produces: `SearchWebArgs.queries: list[str]`（最多 4）+ `top_k: int`

- [ ] **Step 1: 写失败测试**

```python
import pytest
from src.agents.tools.web_tools import SearchWebArgs, search_web


def test_search_web_args_schema():
    args = SearchWebArgs(queries=["腾讯 2023 年报 业绩", "腾讯 2025 年 业绩"])
    assert len(args.queries) == 2


def test_search_web_multiquery_uses_one_quota(monkeypatch):
    # mock tavily_search 返回空 → 断言 web_count 只 +1（一次调用多 query）
    ...
```

- [ ] **Step 2: 实现**

```python
class SearchWebArgs(BaseModel):
    """search_web 工具参数（多查询：一次调用覆盖多个搜索目标）。"""

    queries: list[str] = Field(
        description="搜索查询列表（最多 4 个），一次调用并行搜索并合并结果"
    )
    top_k: int = Field(default=5, ge=1, le=10, description="每个查询返回条数上限")


async def search_web(queries: list[str], top_k: int = 5) -> str:
    """...（多 query 并行 tavily_search，合并统一编号，占 1 次 web_count）"""
    ctx = current_request_ctx.get()
    if ctx is None:
        return SSEInteractionTexts.ASK_USER_CTX_UNAVAILABLE
    if ctx.web_count >= settings.WEB_SEARCH_PER_TURN_LIMIT:
        return SSEInteractionTexts.WEB_SEARCH_LIMIT_TEXT
    ctx.web_count += 1  # 一次多查询调用只占 1 次额度
    queries = queries[:4]
    results_list = await asyncio.gather(
        *[tavily_search(q, top_k=top_k, timeout=settings.TAVILY_TIMEOUT) for q in queries]
    )
    # 合并所有 query 结果，统一编号写入 tool_contexts（kind=web），返回 "[n] 来源:...\n内容:..."
```

- [ ] **Step 3: 跑通过**

Run: `pytest tests/agents/tools/test_web_tools.py -v`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add src/agents/tools/web_tools.py tests/agents/tools/test_web_tools.py
git commit -m "feat: search_web 升级 queries 数组（多查询一次调用占 1 次额度）"
```

---

## Part E: 聊天页 UI（spec: chat-harness-ui）

**实施方式**：走 `frontend-design` skill 按 `docs/design/pages/chat-harness.md` + `docs/design/chat-harness-mockup.html` 落地到 `deploy/nginx/html/chat.html`；每步用 playwright-cli 打开验证。

### Task E1: 侧栏结构重构

- [ ] E1.1 按设计稿改侧栏：头部（Logo+品牌+版本号）/ 功能块（新建会话+知识库管理）/ 会话历史 / 登录头像
- [ ] E1.2 playwright 打开 chat.html，核对侧栏结构与浅色系

### Task E2: 双页面形态

- [ ] E2.1 新对话页（居中品牌+标题+居中输入框，无示例问题）+ 历史对话页（顶栏+消息流+底部输入框）
- [ ] E2.2 侧栏"新建会话"/会话项切换逻辑

### Task E3: 知识库选择器

- [ ] E3.1 新对话页输入框上方 KB 胶囊 + 单选弹窗（`POST /api/kbs/list` 数据源）
- [ ] E3.2 默认"选择知识库"/勾选显示 KB 名/取消显示"请选择知识库"；单选可取消
- [ ] E3.3 playwright 验证勾选/取消交互

### Task E4: KB 会话级绑定

- [ ] E4.1 新建会话选定 KB 后随请求传 kb_id（save_session 落库）；历史对话页不再显示选择器
- [ ] E4.2 顶栏显示 `会话名 知识库:XXX`（未绑定只显示会话名）

### Task E5: 消息流样式

- [ ] E5.1 用户浅灰气泡（#F1F5F9）/ AI markdown 回复 + 模型标注（由 {model} 回答）/ 去时间戳 / 保留引用卡与工具状态
- [ ] E5.2 playwright 对照 mockup 核对

### Task E6: 提交

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat: 聊天页 UI 重构（chat-harness 设计稿落地）"
```

---

## Part F: 质量门禁与契约同步

### Task F1: 全量质量门禁

- [ ] F1.1 `pytest tests/ -v` 全量通过
- [ ] F1.2 `ruff check .` 无错误；`pyright src/` 不引入新 error
- [ ] F1.3 `docs/agents/api_contract.md` / `docs/agents/glossary.md` 更新 `kb_id` 空串语义（"搜索所有知识库"→"不检索"）
- [ ] F1.4 手动验证："腾讯这几年业绩怎么样"（绑定腾讯 KB）→ 解析 [2023,2024,2025]、缺失询问"是否联网"、确认后多 query 联网补充、拒绝则标注"仅覆盖 2024"
- [ ] F1.5 提交

```bash
git add -A
git commit -m "chore: agent-harness-foundation 质量门禁收尾"
```

---

## Self-Review 记录

- **Spec 覆盖**：temporal-constraint（B1-B4）✓、answer-verification（C1-C3）✓、tool-registry（D1-D3）✓、chat-harness-ui（E1-E6）✓、CLAUDE.md 认知层（A1）✓、契约同步（F1.3）✓
- **决策覆盖**：正则粗筛触发（B2/B4）✓、KB=RAG 开关（D2）✓、跨库废弃（D2.3）✓、询问确认 + web_confirmed（C2）✓、judge 最终答案跑（C1/C2）✓、search_web 多查询（D3）✓、纯对话轻量自检（C3）✓、独立计数（C2 `_ask_web_confirm`）✓
- **遗留（P1）**：kb_router 下移、错误分类重试、输出护栏、上下文压缩、检索质量评估（不在本 plan）
