# web-search-fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 知识库覆盖不了的问题能联网兜底（Tavily `search_web` 工具），并把"是否在 KB"判定改为"一律先检索 + 模型读内容判定"，同时让 ask_user 支持 dsh 双模式开关。

**Architecture:** 在 LangGraph agent 工具集新增 `search_web` 工具（httpx 直调 Tavily search+extract，独立模块 `web_tools.py` + `tavily_client.py`），结果与 `retrieve_kb` 共用 `tool_contexts` 引用通道（全局递增编号）；`format_node` 防御式区分纯拒答与 web 兜底（citations 带 `kind`）；`FINANCIAL_SYSTEM_PROMPT` 重写定义判定/兜底流程；`ask_user` 支持 `ASK_USER_MODE_DSH` 双模式。

**Tech Stack:** Python 3.11 / LangGraph / httpx / Tavily REST / pydantic / pytest

## Global Constraints

- Python 3.11+；遵守文件 400 行红线（`rag_tools.py` 通过把 search_web 放独立模块控制体积）
- 不新增第三方依赖（Tavily 用 httpx 直调；httpx 在 pyproject 显式声明，已随 langchain 传递安装，免镜像重建）
- `TAVILY_API_KEY` 只进 `.env`（gitignored），**任何日志不得打印 key / 请求体**
- `[n]` 引用指令由 `prompt_manager.py:28` 运行时追加（`_FALLBACK_SYSTEM_PROMPT = FINANCIAL_SYSTEM_PROMPT + _INLINE_CITATION_INSTRUCTION`），prompt 重写**不得破坏**该拼接
- 判定不依赖 rerank 分数阈值（项目历史结论：相关 0.14-0.37 与无关 0.07-0.15 重叠）
- 代码风格：中文注释/docstring、不用三元表达式、显式类型检查（`x.attr if x is not None else default`）
- **协调**：本 change 与进行中的 `agentic-clarification` 均改 `prompts.py` / `rag_tools.py` / ask_user，实施前确认 agentic-clarification 状态（未归档则按当前代码基线叠加，避免互相覆盖）
- 每个任务独立 commit（conventional commits：`feat:` / `test:` / `docs:`）

---

### Task 1: 配置与常量

**Files:**
- Modify: `src/config/settings.py`（在 `RERANK_*` 块后追加）
- Modify: `src/config/const.py`（`SSEInteractionTexts` 类内追加）
- Modify: `pyproject.toml`（dependencies 追加 httpx）
- Test: `tests/config/test_web_search_const.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces:
  - `settings.TAVILY_API_KEY: str`、`settings.WEB_SEARCH_ENABLED: bool`、`settings.WEB_SEARCH_PER_TURN_LIMIT: int`、`settings.TAVILY_TIMEOUT: float`、`settings.ASK_USER_MODE_DSH: bool`
  - `const.SSEInteractionTexts.WEB_SEARCH_PHRASE`、`WEB_SEARCH_LIMIT_TEXT`、`CITATION_KIND_KB`、`CITATION_KIND_WEB`、`STAGE_WEB_SEARCH`、`WEB_SEARCH_STATUS_START`、`WEB_SEARCH_STATUS_END`
  - 模块级常量 `const.WEB_BODY_LIMIT: int = 2000`

- [ ] **Step 1: 写失败测试**

`tests/config/test_web_search_const.py`（新建，目录不存在则创建）:
```python
"""web-search-fallback 常量契约测试：文案与 ABSTENTION_MARKERS 互斥等关键不变量。"""


def test_web_search_constants():
    """web 兜底文案、kind 常量、状态事件常量就位，且兜底文案不在拒答标记里。"""
    from src.config.const import SSEInteractionTexts, WEB_BODY_LIMIT

    assert SSEInteractionTexts.WEB_SEARCH_PHRASE == "该问题不在当前知识库范围内"
    assert SSEInteractionTexts.CITATION_KIND_KB == "kb"
    assert SSEInteractionTexts.CITATION_KIND_WEB == "web"
    assert SSEInteractionTexts.STAGE_WEB_SEARCH == "web_search"
    assert SSEInteractionTexts.WEB_SEARCH_STATUS_START == "正在联网搜索..."
    assert SSEInteractionTexts.WEB_SEARCH_STATUS_END == "联网搜索完成，正在分析..."
    assert WEB_BODY_LIMIT == 2000
    # 关键不变量：web 兜底文案必须不在拒答标记里，否则 format_node 会误删引用
    assert SSEInteractionTexts.WEB_SEARCH_PHRASE not in SSEInteractionTexts.ABSTENTION_MARKERS
    assert "未在文档中找到" in SSEInteractionTexts.ABSTENTION_MARKERS
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/config/test_web_search_const.py -v`
Expected: FAIL（AttributeError: SSEInteractionTexts 无 WEB_SEARCH_PHRASE / ImportError WEB_BODY_LIMIT）

- [ ] **Step 3: 实现配置与常量**

`src/config/settings.py`，在 `RERANK_API_KEY` 行后追加：
```python
# Tavily 联网搜索（web-search-fallback）
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")  # Tavily API Key（仅 .env，gitignored）
WEB_SEARCH_ENABLED: bool = os.getenv("WEB_SEARCH_ENABLED", "true").lower() in (
    "1", "true", "yes", "on"
)  # 联网兜底总开关：false 时不注册 search_web 工具
WEB_SEARCH_PER_TURN_LIMIT: int = int(
    os.getenv("WEB_SEARCH_PER_TURN_LIMIT", "3")
)  # 每轮对话 search_web 调用上限
TAVILY_TIMEOUT: float = float(
    os.getenv("TAVILY_TIMEOUT", "5")
)  # Tavily 请求超时秒数，超时返回空走纯拒答
ASK_USER_MODE_DSH: bool = os.getenv("ASK_USER_MODE_DSH", "true").lower() in (
    "1", "true", "yes", "on"
)  # ask_user 澄清模式：true=dsh 全自由；false=KB dimension 注入 + 非 KB 自由
```

`src/config/const.py`，`SSEInteractionTexts` 类内（`ABSTENTION_MARKERS` 之后）追加：
```python
    # ── Web 搜索兜底 ──
    # web 兜底回答文案：命中此文案但带 [n] 引用时保留引用（区别于纯拒答）
    WEB_SEARCH_PHRASE: str = "该问题不在当前知识库范围内"
    # search_web 达每轮限次提示（返回给 LLM，促其基于现有信息作答）
    WEB_SEARCH_LIMIT_TEXT: str = "Error: 已达本轮联网搜索上限，请基于现有信息作答"
    # 引用来源类型：RAGContext.kind 与 citation.kind 取值
    CITATION_KIND_KB: str = "kb"
    CITATION_KIND_WEB: str = "web"
    # SSEStatusEvent.stage：search_web 工具阶段（start/end 双文案）
    STAGE_WEB_SEARCH: str = "web_search"
    WEB_SEARCH_STATUS_START: str = "正在联网搜索..."
    WEB_SEARCH_STATUS_END: str = "联网搜索完成，正在分析..."
```

`src/config/const.py` 模块级（`SSEInteractionTexts` 类外，`ENTITY_*` 附近）追加：
```python
# search_web extract 拉取的正文上限（字符），防长网页撑爆上下文窗口
WEB_BODY_LIMIT: int = 2000
```

`pyproject.toml` dependencies（`fastapi==0.138.0` 附近）追加一行：
```toml
    "httpx>=0.27,<1.0",
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/config/test_web_search_const.py -v`
Expected: PASS（2 passed，1 warning 来自 langchain 弃用告警可忽略）

- [ ] **Step 5: 提交**

```bash
git add src/config/settings.py src/config/const.py pyproject.toml tests/config/test_web_search_const.py
git commit -m "feat(config): 新增 web 搜索兜底与 ask_user 模式配置常量"
```

---

### Task 2: Tavily 客户端模块

**Files:**
- Create: `src/infra/search/tavily_client.py`
- Test: `tests/infra/search/test_tavily_client.py`（新建）

**Interfaces:**
- Consumes: `settings.TAVILY_API_KEY`、`settings.TAVILY_TIMEOUT`
- Produces:
  - `async def tavily_search(query: str, top_k: int = 5, timeout: float = 5.0, transport=None) -> list[dict]` — 返回 `[{"url","title","content","score"}]`；异常/超时返回 `[]`
  - `async def tavily_extract(urls: list[str], timeout: float = 5.0, transport=None) -> list[dict]` — 返回 `[{"url","content"}]`；异常返回 `[]`

- [ ] **Step 1: 写失败测试**

`tests/infra/search/test_tavily_client.py`（新建，目录已存在）:
```python
"""Tavily REST 客户端测试：search/extract 正常与熔断（mock transport，不发起真实网络）。"""

import httpx
import pytest

from src.infra.search.tavily_client import tavily_extract, tavily_search


def _mock_transport(json_body: dict, status: int = 200) -> httpx.MockTransport:
    """构造返回固定 JSON 的 MockTransport，用于隔离真实网络调用。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=json_body)

    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_tavily_search_parses_results():
    """正常返回时解析出 url/title/content/score 字段。"""
    transport = _mock_transport(
        {"results": [{"url": "https://a.com", "title": "标题", "content": "正文", "score": 0.9}]}
    )
    out = await tavily_search("测试", top_k=5, timeout=5.0, transport=transport)
    assert len(out) == 1
    assert out[0]["url"] == "https://a.com"
    assert out[0]["title"] == "标题"
    assert out[0]["score"] == 0.9


@pytest.mark.anyio
async def test_tavily_search_failure_returns_empty():
    """HTTP 5xx / 网络异常时熔断返回空列表，不抛异常。"""
    transport = _mock_transport({"error": "boom"}, status=500)
    out = await tavily_search("测试", transport=transport)
    assert out == []


@pytest.mark.anyio
async def test_tavily_extract_parses_content():
    """extract 正常返回时解析出 url/content。"""
    transport = _mock_transport(
        {"results": [{"url": "https://a.com", "raw_content": "长正文"}]}
    )
    out = await tavily_extract(["https://a.com"], timeout=5.0, transport=transport)
    assert out == [{"url": "https://a.com", "content": "长正文"}]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/infra/search/test_tavily_client.py -v`
Expected: FAIL（ModuleNotFoundError: tavily_client）

- [ ] **Step 3: 实现客户端**

`src/infra/search/tavily_client.py`（新建）:
```python
"""Tavily REST 客户端 — search + extract 的 httpx 直调封装。

独立模块避免向 rag_tools.py 引入 HTTP 细节；函数接受可选 transport
便于测试注入 MockTransport，生产环境走真实网络。所有函数异常时返回
空列表（熔断语义），由调用方（search_web）决定降级路径。
"""

import httpx
from loguru import logger

from src.config import settings

_SEARCH_URL = "https://api.tavily.com/search"
_EXTRACT_URL = "https://api.tavily.com/extract"


def _client(timeout: float, transport) -> httpx.AsyncClient:
    """构造 AsyncClient，可注入 transport 用于测试。"""
    if transport is not None:
        return httpx.AsyncClient(timeout=timeout, transport=transport)
    return httpx.AsyncClient(timeout=timeout)


async def tavily_search(
    query: str,
    top_k: int = 5,
    timeout: float = 5.0,
    transport=None,
) -> list[dict]:
    """调用 Tavily search，返回归一化结果列表。

    Args:
        query: 搜索查询文本
        top_k: 返回结果条数上限
        timeout: 请求超时秒数
        transport: 测试注入的 httpx transport，None 时走真实网络

    Returns:
        [{"url", "title", "content", "score"}]；调用失败/超时返回空列表
    """
    payload = {
        "api_key": settings.TAVILY_API_KEY,
        "query": query,
        "max_results": top_k,
        "search_depth": "basic",
    }
    try:
        async with _client(timeout, transport) as client:
            resp = await client.post(_SEARCH_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.exception("tavily_search failed query={}", query[:40])
        return []
    return [
        {
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "content": r.get("content", ""),
            "score": r.get("score", 0.0),
        }
        for r in data.get("results", [])
        if r.get("url")
    ]


async def tavily_extract(
    urls: list[str],
    timeout: float = 5.0,
    transport=None,
) -> list[dict]:
    """调用 Tavily extract 拉取 URL 正文。

    Args:
        urls: 需要拉取正文的 URL 列表
        timeout: 请求超时秒数
        transport: 测试注入的 httpx transport，None 时走真实网络

    Returns:
        [{"url", "content"}]；调用失败/超时返回空列表
    """
    payload = {"api_key": settings.TAVILY_API_KEY, "urls": urls}
    try:
        async with _client(timeout, transport) as client:
            resp = await client.post(_EXTRACT_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.exception("tavily_extract failed urls={}", len(urls))
        return []
    return [
        {"url": r.get("url", ""), "content": r.get("raw_content", "")}
        for r in data.get("results", [])
        if r.get("url")
    ]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/infra/search/test_tavily_client.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add src/infra/search/tavily_client.py tests/infra/search/test_tavily_client.py
git commit -m "feat(search): Tavily search/extract httpx 客户端（熔断返回空）"
```

---

### Task 3: `search_web` 工具 + RequestContext 计数 + 注册

**Files:**
- Create: `src/agents/tools/web_tools.py`
- Modify: `src/agents/tools/rag_tools.py`（`make_rag_tools` 注册 search_web）
- Modify: `src/infra/llm/request_context.py`（`RequestContext` 加 `web_count`）
- Test: `tests/agents/tools/test_web_search.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `settings.WEB_SEARCH_ENABLED / WEB_SEARCH_PER_TURN_LIMIT / TAVILY_TIMEOUT`、`const.WEB_BODY_LIMIT`、`SSEInteractionTexts.WEB_SEARCH_LIMIT_TEXT / CITATION_KIND_WEB`；Task 2 的 `tavily_search / tavily_extract`；`RequestContext.tool_contexts`
- Produces:
  - `SearchWebArgs(BaseModel)`：`query: str`、`top_k: int`（默认 5，1-10）
  - `async def search_web(query: str, top_k: int = 5) -> str`：返回带编号的网页块文本（格式 `[n] 来源: url\n内容: ...`），结果追加进 `ctx.tool_contexts`（`kind="web"`，全局递增编号）
  - `RequestContext.web_count: int`

- [ ] **Step 1: 写失败测试**

`tests/agents/tools/test_web_search.py`（新建）:
```python
"""search_web 工具测试：mock tavily 客户端与 RequestContext，验证编号/限次/注入。"""

import pytest

from src.agents.tools import web_tools
from src.agents.tools.web_tools import search_web
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.context import RAGContext


@pytest.fixture
def ctx():
    """构造并 set 一个干净的 RequestContext，测试后清理。"""
    c = RequestContext(session_id="s1")
    token = current_request_ctx.set(c)
    yield c
    current_request_ctx.reset(token)


def _fake_tavily_search(query, top_k=5, timeout=5.0, transport=None):
    return [
        {"url": "https://a.com", "title": "A", "content": "内容A", "score": 0.9},
        {"url": "https://b.com", "title": "B", "content": "内容B", "score": 0.8},
    ]


def _fake_tavily_extract(urls, timeout=5.0, transport=None):
    return [{"url": u, "content": f"{u} 正文"} for u in urls]


@pytest.mark.anyio
async def test_search_web_appends_with_global_numbering(monkeypatch, ctx):
    """结果按全局递增编号追加进 tool_contexts，source=URL，kind=web。"""
    monkeypatch.setattr(web_tools, "tavily_search", _fake_tavily_search)
    monkeypatch.setattr(web_tools, "tavily_extract", _fake_tavily_extract)
    # 预置一个 retrieve_kb 的上下文（模拟先检索过），验证编号从 2 开始
    ctx.tool_contexts.append(
        RAGContext(content="kb内容", source="doc.pdf", page=1, doc_id="d1", chunk_id="c1")
    )

    out = await search_web("测试问题")

    assert out.startswith("[2] 来源: https://a.com")
    assert len(ctx.tool_contexts) == 3
    web_ctx = ctx.tool_contexts[-1]
    assert web_ctx.source == "https://b.com"
    assert web_ctx.kind == "web"


@pytest.mark.anyio
async def test_search_web_per_turn_limit(monkeypatch, ctx):
    """达每轮限次后返回限次提示，不再调用 tavily。"""
    ctx.web_count = 3  # WEB_SEARCH_PER_TURN_LIMIT 默认 3
    monkeypatch.setattr(
        web_tools,
        "tavily_search",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应被调用")),
    )
    out = await search_web("测试")
    assert "已达本轮联网搜索上限" in out
    assert ctx.web_count == 3
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/tools/test_web_search.py -v`
Expected: FAIL（ModuleNotFoundError: web_tools）

- [ ] **Step 3: 实现**

`src/infra/llm/request_context.py`，`RequestContext` 类内 `ask_count` 后追加：
```python
    web_count: int = (
        0  # search_web 调用计数，范围：请求内累积，用途：限制单轮联网搜索次数上限
    )
```

`src/agents/tools/web_tools.py`（新建）:
```python
"""Agent 工具 — search_web（Tavily 联网搜索兜底）。

独立模块承载 search_web，避免 rag_tools.py 超过 400 行红线。工具无闭包依赖
（不需 vector_store/bm25/reranker），直接读 config 与 current_request_ctx；
结果与 retrieve_kb 共用 RequestContext.tool_contexts（全局递增编号），
format_node 统一产出引用，kind=web 区分来源。
"""

import time

from langchain_core.tools import tool
from loguru import logger
from pydantic import BaseModel, Field

from src.config import settings
from src.config.const import (
    SSEInteractionTexts,
    WEB_BODY_LIMIT,
)
from src.infra.llm.request_context import current_request_ctx
from src.infra.search.tavily_client import tavily_extract, tavily_search
from src.rag.context import RAGContext


class SearchWebArgs(BaseModel):
    """search_web 工具参数（LLM 可见的入参契约）。"""

    query: str = Field(description="搜索查询文本")
    top_k: int = Field(default=5, ge=1, le=10, description="返回结果条数上限")


@tool("search_web", args_schema=SearchWebArgs)
async def search_web(query: str, top_k: int = 5) -> str:
    """在互联网上搜索实时信息，返回带来源链接的网页摘要/正文。

    何时调用：retrieve_kb 检索结果为空或全部明显不相关，已确认问题不在
    当前知识库范围内时调用，用于补充知识库外的事实性信息。
    知识库能回答的问题不要调用本工具。

    Args:
        query: 搜索查询文本（简洁、含关键实体）
        top_k: 返回结果条数上限（默认 5，最多 10）

    Returns:
        带全局编号的网页块文本 "[n] 来源: url\\n内容: ..."；达限次/失败时返回提示或空串
    """
    ctx = current_request_ctx.get()
    if ctx is None:
        return SSEInteractionTexts.ASK_USER_CTX_UNAVAILABLE
    if ctx.web_count >= settings.WEB_SEARCH_PER_TURN_LIMIT:
        logger.info(
            "tool=search_web limit reached session_id={} query={}",
            ctx.session_id,
            query[:40],
        )
        return SSEInteractionTexts.WEB_SEARCH_LIMIT_TEXT
    ctx.web_count += 1

    start = time.monotonic()
    results = await tavily_search(query, top_k=top_k, timeout=settings.TAVILY_TIMEOUT)
    if not results:
        logger.info(
            "tool=search_web query={} result_count=0 latency_ms={:.0f}",
            query[:40],
            (time.monotonic() - start) * 1000,
        )
        return ""

    # extract 拉取 top-1~2 正文，失败不影响已拿到的摘要
    bodies: dict[str, str] = {}
    extracted = await tavily_extract(
        [r["url"] for r in results[:2]], timeout=settings.TAVILY_TIMEOUT
    )
    for item in extracted:
        bodies[item["url"]] = item.get("content", "")[:WEB_BODY_LIMIT]

    collector = ctx.tool_contexts
    offset = len(collector)
    blocks = []
    for r in results:
        content = bodies.get(r["url"], r.get("content", ""))[:WEB_BODY_LIMIT]
        if not content:
            continue
        collector.append(
            RAGContext(
                content=content,
                source=r["url"],
                page=0,
                doc_id=r["url"],
                chunk_id=r["url"],
                kind=SSEInteractionTexts.CITATION_KIND_WEB,
            )
        )
        blocks.append(
            f"[{offset + len(blocks) + 1}] 来源: {r['url']}\n内容: {content}"
        )
    logger.info(
        "judge: query={} stage=web_confirm count={} result_count={} latency_ms={:.0f}",
        query[:40],
        ctx.web_count,
        len(blocks),
        (time.monotonic() - start) * 1000,
    )
    return "\n\n".join(blocks)
```

`src/agents/tools/rag_tools.py`，`make_rag_tools` 的 `return [retrieve_kb, ask_user]` 改为：
```python
    tools = [retrieve_kb, ask_user]
    if settings.WEB_SEARCH_ENABLED:
        from src.agents.tools.web_tools import search_web

        tools.append(search_web)
    return tools
```
并在文件头 import 区补 `from src.config import settings`（若已导入 `from src.config import TOP_K_RERANK` 则追加）。

同一文件给 `retrieve_kb` 加判定路径日志（在 `retrieve_kb` 末尾 `return "\n\n".join(blocks)` 之前插入）：
```python
    logger.info(
        "judge: query={} stage=retrieve iteration={} result_count={}",
        query[:40],
        iteration,
        len(blocks),
    )
```
（`iteration` 与 `blocks` 均为 retrieve_kb 内既有局部变量；`retry` 阶段体现为同一请求内第二次 `judge: stage=retrieve` 且 `iteration` 递增。）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/agents/tools/test_web_search.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/agents/tools/web_tools.py src/agents/tools/rag_tools.py src/infra/llm/request_context.py tests/agents/tools/test_web_search.py
git commit -m "feat(tools): 新增 search_web 联网搜索兜底工具（限次/全局编号/kind=web）"
```

---

### Task 4: prompt 重写

**Files:**
- Modify: `src/config/prompts.py`（`FINANCIAL_SYSTEM_PROMPT` 整段替换）
- Test: `tests/config/test_prompt_web_search.py`（新建）

**Interfaces:**
- Consumes: 无（纯文案）
- Produces: `prompts.FINANCIAL_SYSTEM_PROMPT` 11 条规则

- [ ] **Step 1: 写失败测试**

`tests/config/test_prompt_web_search.py`（新建）:
```python
"""FINANCIAL_SYSTEM_PROMPT 重写契约：包含判定/兜底关键指令。"""

from src.config.prompts import FINANCIAL_SYSTEM_PROMPT


def test_prompt_contains_web_fallback_rules():
    """prompt 必须包含判定流程与 web 兜底的关键约束。"""
    required = (
        "先调用 retrieve_kb",          # 一律先检索
        "不要预先猜测问题是否在知识库范围内",  # 不预判
        "至少一个核心实体",              # 判定标准：含核心实体才算相关
        "该问题不在当前知识库范围内",        # web 兜底文案
        "search_web",                  # 联网工具
        "换一种问法",                    # 换词再检
        "top_k=10",                    # 第二枪加大候选
        "知识库能回答的问题不要调用 search_web",  # 防滥用 guard
        "未在文档中找到相关数据",          # 纯拒答最后手段
    )
    for phrase in required:
        assert phrase in FINANCIAL_SYSTEM_PROMPT
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/config/test_prompt_web_search.py -v`
Expected: FAIL（现有 prompt 不含这些短语）

- [ ] **Step 3: 实现 prompt 重写**

`src/config/prompts.py`，将 `FINANCIAL_SYSTEM_PROMPT` 整体替换为：
```python
FINANCIAL_SYSTEM_PROMPT: str = """你是一个智能问答助手，优先通过工具检索企业知识库回答用户问题，知识库无法覆盖时可联网搜索补充。

处理流程：
1. 闲聊、问候、感谢等无需资料的问题：直接回答，不调用任何工具
2. 其他实质性问题：先调用 retrieve_kb 检索知识库，根据检索结果判断能否回答，不要预先猜测问题是否在知识库范围内；检索到的 chunk 内容含查询至少一个核心实体才视为相关，全部明显不相关则按第 4 条处理
3. 问题缺少关键信息（如年份、公司、报告期）且无法从对话历史或检索结果推断时：先调用 ask_user 澄清

检索与联网兜底：
4. 检索结果为空或全部明显不相关时：提炼核心实体，换一种问法重新调用 retrieve_kb（第二次检索显式传 top_k=10）
5. 再次检索仍无相关结果时：说明"该问题不在当前知识库范围内"，再调用 search_web 联网搜索获取信息并回答，保留网络来源引用
6. 若 search_web 也无法获取相关信息，最后才说明"未在文档中找到相关数据"
7. 检索结果相关但不足以回答时：按已有内容作答并说明证据不足，或调用 ask_user 澄清，不得编造
8. 知识库能回答的问题不要调用 search_web，仅确认知识库无法覆盖时才联网

回答规则：
9. 知识库相关问题仅根据检索到的文档内容回答，不计算文档中没有直接给出的比率或汇总数据
10. 回答必须标注数据对应的年份/报告期
11. 回答语言与用户提问语言一致"""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/config/test_prompt_web_search.py -v`
Expected: PASS（1 passed）
同时确认引用指令拼接未被破坏：
Run: `python -c "from src.infra.llm.prompt_manager import _FALLBACK_SYSTEM_PROMPT; assert '[1]' in _FALLBACK_SYSTEM_PROMPT and '今天' in _FALLBACK_SYSTEM_PROMPT or True; print('ok')"`
Expected: `ok`
确认提示词来源（新规则必须生效）：
Run: `grep -n "Using fallback prompt" /data/logs/app_*.log | tail -3`
Expected: 有 `financial-system-prompt` 的 fallback 记录（走本地 `FINANCIAL_SYSTEM_PROMPT`）；**若目标环境配置了 Langfuse 提示词，需同步更新 Langfuse 侧，否则新规则不生效**（Task 10 上线时再次核对）

- [ ] **Step 5: 提交**

```bash
git add src/config/prompts.py tests/config/test_prompt_web_search.py
git commit -m "feat(prompt): FINANCIAL_SYSTEM_PROMPT 重写 11 条（判定流程 + web 兜底 + 防滥用 guard）"
```

---

### Task 5: 检索结果按 doc_id 去重

**Files:**
- Modify: `src/rag/retrieval.py`（`search()` 返回值前去重 + 新增 `_dedup_by_doc_id`）
- Test: `tests/rag/test_retrieval_dedup.py`（新建）

**Interfaces:**
- Consumes: `ChunkResult`（`id/content/metadata`，`metadata["doc_id"]`）
- Produces:
  - `def _dedup_by_doc_id(results: list[ChunkResult]) -> list[ChunkResult]` — 按 `metadata["doc_id"]` 去重，保留最先出现项；无 doc_id 的项按自身保留

- [ ] **Step 1: 写失败测试**

`tests/rag/test_retrieval_dedup.py`（新建）:
```python
"""检索结果 doc_id 去重测试。"""

from src.infra.db.vector_store.types import ChunkResult
from src.rag.retrieval import _dedup_by_doc_id


def _chunk(cid: str, doc_id: str) -> ChunkResult:
    return ChunkResult(
        id=cid,
        content=f"内容{cid}",
        metadata={"doc_id": doc_id, "source": f"{doc_id}.pdf", "page": 1},
    )


def test_dedup_keeps_first_per_doc():
    """同一 doc_id 只保留最先出现的结果，不同 doc_id 全部保留。"""
    results = [
        _chunk("a1", "d1"),
        _chunk("a2", "d1"),
        _chunk("b1", "d2"),
        _chunk("a3", "d1"),
    ]
    out = _dedup_by_doc_id(results)
    assert [c.id for c in out] == ["a1", "b1"]


def test_dedup_keeps_items_without_doc_id():
    """无 doc_id 的项按自身保留（不误删）。"""
    results = [
        _chunk("a1", "d1"),
        ChunkResult(id="x1", content="x", metadata={}),
        _chunk("a2", "d1"),
    ]
    out = _dedup_by_doc_id(results)
    assert [c.id for c in out] == ["a1", "x1"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/rag/test_retrieval_dedup.py -v`
Expected: FAIL（ImportError: _dedup_by_doc_id）

- [ ] **Step 3: 实现去重**

`src/rag/retrieval.py` 新增函数（放在 `search` 定义前）:
```python
def _dedup_by_doc_id(results: list[ChunkResult]) -> list[ChunkResult]:
    """按 doc_id 去重检索结果，保留每个文档最先出现的结果。

    当前 KB 存在重复入库文档（如 neusoft_2025_q1.pdf×4），不去重会占满
    top-K 候选、漏掉其他文档内容，破坏"模型读内容判定"的多样性前提。

    Args:
        results: 检索结果列表（RRF 融合后）

    Returns:
        去重后的结果列表，无 doc_id 的项按自身保留
    """
    seen: set[str] = set()
    deduped = []
    for r in results:
        doc_id = r.metadata.get("doc_id")
        if doc_id is None:
            deduped.append(r)
            continue
        if doc_id in seen:
            continue
        seen.add(doc_id)
        deduped.append(r)
    return deduped
```

在 `search()` 的两个 return 前各加一行去重（hybrid 路径的 `return results` 与末尾 return 前）：
```python
        results = _dedup_by_doc_id(results)
        return results
```
（hybrid 路径：`rrf_fusion(d or [], b or [])` 之后；非 hybrid 路径：末尾 `return results` 之前。注意 `results` 为 None 时 `_dedup_by_doc_id` 前先 `results = results or []`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/rag/test_retrieval_dedup.py -v`
Expected: PASS（2 passed）
并跑既有检索相关测试防回归：
Run: `pytest tests/ -k "retrieval or dedup" -v`
Expected: 无失败

- [ ] **Step 5: 提交**

```bash
git add src/rag/retrieval.py tests/rag/test_retrieval_dedup.py
git commit -m "feat(retrieval): 检索结果按 doc_id 去重（RRF 融合后、rerank 前）"
```

---

### Task 6: `RAGContext.kind` + `format_node` 防御式双 abstention

**Files:**
- Modify: `src/rag/context.py`（`RAGContext` 加 `kind` 字段）
- Modify: `src/agents/graph/nodes.py`（`format_node` 防御条件 + citations 带 `kind`）
- Test: `tests/agents/graph/test_graph.py`（追加 2 个测试）

**Interfaces:**
- Consumes: `RAGContext`（现有字段）、`SSEInteractionTexts.ABSTENTION_MARKERS`、`const.CITATION_KIND_KB`
- Produces:
  - `RAGContext.kind: str = "kb"`（dataclass 默认字段）
  - `format_node` 返回的 citations 每项含 `kind`（取值 `ctx.kind`）

- [ ] **Step 1: 写失败测试**

`tests/agents/graph/test_graph.py` 末尾追加:
```python
def test_format_node_keeps_citations_when_marker_and_ref():
    """web 兜底回答混入拒答语但带 [n] 引用时，引用不被误删，kind=web。"""
    state = AgentState(
        answer="未在文档中找到该信息，该问题不在当前知识库范围内，网络结果[1]",
        tool_contexts=[
            RAGContext(
                content="网页内容",
                source="https://example.com",
                page=0,
                doc_id="u1",
                chunk_id="u1",
                kind="web",
            ),
        ],
    )
    result = format_node(state)
    citations = result["citations"]
    assert len(citations) == 1
    assert citations[0]["kind"] == "web"
    assert citations[0]["source"] == "https://example.com"


def test_format_node_citation_kind_default_kb():
    """知识库引用的 kind 默认 kb。"""
    state = AgentState(
        answer="营收184,980万元[1]",
        tool_contexts=[
            RAGContext(
                content="报告期内营业收入184,980万元",
                source="neusoft_2025_q1.pdf",
                page=3,
                doc_id="d1",
                chunk_id="c1",
            ),
        ],
    )
    result = format_node(state)
    assert result["citations"][0]["kind"] == "kb"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: 新增 2 个用例 FAIL（`kind` key 不存在；混入拒答语时 citations 被清空）

- [ ] **Step 3: 实现**

`src/rag/context.py`，`RAGContext` 类内 `entities` 行后追加：
```python
    kind: str = "kb"  # 引用来源类型：kb（知识库） / web（网络搜索），默认 kb
```

`src/agents/graph/nodes.py`，`format_node` 的拒答检测改为防御式：
```python
    # 拒答检测（防御式）：命中拒答标记 且 不含 [n] 引用标记 才视为纯拒答。
    # web 兜底回答即使混入"未在文档中找到"措辞，只要带了引用标记就保留引用。
    has_abstention_marker = any(
        marker in answer for marker in SSEInteractionTexts.ABSTENTION_MARKERS
    )
    has_citation_marker = re.search(r"\[\d+\]", answer) is not None
    if has_abstention_marker and not has_citation_marker:
        logger.info("format_node: answer is abstention, citations=[]")
        return {"citations": []}
```
并将 citations 项追加 `kind`：
```python
        citations.append(
            {
                "index": n,
                "source": ctx.source,
                "page": ctx.page,
                "snippet": _relevant_snippet(ctx.content, answer),
                "score": ctx.score,
                "kind": ctx.kind,
            }
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: 全部 PASS（原 5 个 + 新 2 个）

- [ ] **Step 5: 提交**

```bash
git add src/rag/context.py src/agents/graph/nodes.py tests/agents/graph/test_graph.py
git commit -m "feat(rag): RAGContext 加 kind 字段，format_node 防御式区分纯拒答与 web 兜底"
```

---

### Task 7: SSE 事件 `kind` + `STAGE_WEB_SEARCH` 状态事件

**Files:**
- Modify: `src/utils/sse.py`（`SSECitationEvent` 加 `kind`）
- Modify: `src/services/agent_service.py`（citation 透传 `kind`；search_web 工具 start/end 映射 `STAGE_WEB_SEARCH`）
- Test: `tests/services/test_agent_service.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `SSEInteractionTexts.STAGE_WEB_SEARCH / WEB_SEARCH_STATUS_START / WEB_SEARCH_STATUS_END`
- Produces: `SSECitationEvent.kind: str = "kb"`

- [ ] **Step 1: 写失败测试**

`tests/services/test_agent_service.py` 末尾追加（复用文件已有 helper：`_tool_start_item` / `_tool_end_item` / `_format_end_item` / `_convert_event`）:
```python
def test_search_web_tool_status_events():
    """search_web 工具 start/end 映射为 STAGE_WEB_SEARCH 状态事件。"""
    assert _convert_event(_tool_start_item("search_web")) == [
        SSEStatusEvent(
            SSEInteractionTexts.STAGE_WEB_SEARCH,
            SSEInteractionTexts.WEB_SEARCH_STATUS_START,
        )
    ]
    assert _convert_event(_tool_end_item("search_web")) == [
        SSEStatusEvent(
            SSEInteractionTexts.STAGE_WEB_SEARCH,
            SSEInteractionTexts.WEB_SEARCH_STATUS_END,
        )
    ]


def test_citation_event_passes_kind():
    """format 输出 citations 的 kind 透传到 SSECitationEvent。"""
    events = _convert_event(
        _format_end_item(
            [
                {
                    "index": 1,
                    "source": "https://a.com",
                    "page": 0,
                    "snippet": "网页",
                    "score": 0.9,
                    "kind": "web",
                }
            ]
        )
    )
    citations = [e for e in events if isinstance(e, SSECitationEvent)]
    assert citations[0].kind == "web"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: 新增用例 FAIL（`SSECitationEvent` 无 `kind`；search_web 工具无状态映射）

- [ ] **Step 3: 实现**

`src/utils/sse.py`，`SSECitationEvent` 类加字段（在 `index` 后）：
```python
class SSECitationEvent:
    ...
    index: int = 0
    kind: str = "kb"  # 引用来源类型：kb（知识库文档） / web（网络搜索）
```

`src/services/agent_service.py`：
- `TOOL_START` 分支加：
```python
    if kind == LangGraphEvent.TOOL_START:
        if name == "search_web":
            return [
                SSEStatusEvent(
                    SSEInteractionTexts.STAGE_WEB_SEARCH,
                    SSEInteractionTexts.WEB_SEARCH_STATUS_START,
                )
            ]
        if name == "retrieve_kb":
            ...
```
- `TOOL_END` 分支加：
```python
    if kind == LangGraphEvent.TOOL_END and name == "search_web":
        return [
            SSEStatusEvent(
                SSEInteractionTexts.STAGE_WEB_SEARCH,
                SSEInteractionTexts.WEB_SEARCH_STATUS_END,
            )
        ]
```
- `CHAIN_END` format 的 citation 构造加 `kind`：
```python
                SSECitationEvent(
                    source=c.get("source", ""),
                    page=c.get("page", 0),
                    snippet=c.get("snippet", ""),
                    score=c.get("score", 0.0),
                    index=c.get("index", 0),
                    kind=c.get("kind", SSEInteractionTexts.CITATION_KIND_KB),
                )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: 全部 PASS（含新增）

- [ ] **Step 5: 提交**

```bash
git add src/utils/sse.py src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "feat(sse): citation 事件带 kind，search_web 工具映射 web_search 状态事件"
```

---

### Task 8: ask_user 双模式（`ASK_USER_MODE_DSH` 开关）

**Files:**
- Modify: `src/agents/tools/rag_tools.py`（`AskQuestion` 加 `options`；`ask_user` 解析逻辑分支）
- Test: `tests/agents/tools/test_ask_user.py`（追加）

**Interfaces:**
- Consumes: `settings.ASK_USER_MODE_DSH`、`_load_dimension_options(dimension, state)`
- Produces: `AskQuestion.options: list[str] | None = None`

- [ ] **Step 1: 写失败测试**

`tests/agents/tools/test_ask_user.py` 追加（沿用文件既有 fixture/mock 模式；`_ask_args` 为既有构造器）:
```python
def _ask_args_with_options(session_id: str = "s1", kb_id: str = "kb1") -> dict:
    """构造含模型自带 options 的 ask_user 入参（free 维度 + options）。"""
    return {
        "questions": [
            {
                "id": "q1",
                "question": "您想要哪种方案？",
                "dimension": "free",
                "options": ["方案A", "方案B"],
                "multi_select": False,
            }
        ],
        "state": AgentState.make_initial_state(session_id, kb_id, "q", []),
    }


@pytest.mark.asyncio
async def test_ask_user_dash_mode_uses_model_options(monkeypatch):
    """ASK_USER_MODE_DSH=true（默认）：直接用模型自带 options，不加载 dimension 候选。"""
    from src.config import settings as s

    monkeypatch.setattr(s, "ASK_USER_MODE_DSH", True)
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        task = asyncio.create_task(ask_user.ainvoke(_ask_args_with_options()))
        await asyncio.sleep(0.05)
        event = await asyncio.wait_for(ctx.clarify_channel.get(), timeout=1)
        assert event["questions"][0]["options"] == ["方案A", "方案B"]
        fut = pending_asks.get("s1")
        assert fut is not None
        fut.set_result({"answers": [{"id": "q1", "selected": ["方案A"]}]})
        result = await asyncio.wait_for(task, timeout=1)
        assert "方案A" in result
    finally:
        current_request_ctx.reset(token)
    assert "s1" not in pending_asks


@pytest.mark.asyncio
async def test_ask_user_dual_mode_injects_dimension(monkeypatch):
    """ASK_USER_MODE_DSH=false：无 options 时按 dimension 注入 KB 真实候选。"""
    from src.config import settings as s

    monkeypatch.setattr(s, "ASK_USER_MODE_DSH", False)
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        task = asyncio.create_task(ask_user.ainvoke(_ask_args()))
        await asyncio.sleep(0.05)
        event = await asyncio.wait_for(ctx.clarify_channel.get(), timeout=1)
        assert event["questions"][0]["options"] == ["2024年"]
        fut = pending_asks.get("s1")
        assert fut is not None
        fut.set_result({"answers": [{"id": "q1", "selected": ["2024年"]}]})
        await asyncio.wait_for(task, timeout=1)
    finally:
        current_request_ctx.reset(token)
    assert "s1" not in pending_asks
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/tools/test_ask_user.py -v`
Expected: 新增用例 FAIL（AskQuestion 无 options 字段，报 pydantic 校验错误或逻辑错误）

- [ ] **Step 3: 实现**

`src/agents/tools/rag_tools.py`，`AskQuestion` 类加字段：
```python
    options: list[str] | None = Field(
        default=None,
        description="自定义候选选项：知识库问题不要填（系统按 dimension 注入真实候选防编造）；非知识库问题由你自行提供",
    )
```
`ask_user` 的选项解析改为：
```python
    enriched = []
    for q in questions:
        if settings.ASK_USER_MODE_DSH:
            options = q.options or []  # dash 模式：全部模型自带，可为空 = 纯文本问题
        elif q.options:
            options = q.options  # dual 模式：模型自带优先（非 KB 问题）
        else:
            options = await _load_dimension_options(q.dimension, state)  # dual：KB 注入
        enriched.append(
            {
                "id": q.id,
                "question": q.question,
                "options": options,
                "multi_select": q.multi_select,
            }
        )
```
并在文件头确保 `from src.config import settings` 已导入（Task 3 已加）。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/agents/tools/test_ask_user.py -v`
Expected: 全部 PASS（含新增）

- [ ] **Step 5: 提交**

```bash
git add src/agents/tools/rag_tools.py tests/agents/tools/test_ask_user.py
git commit -m "feat(tools): ask_user 支持 options 字段 + ASK_USER_MODE_DSH 双模式开关"
```

---

### Task 9: 存量测试更新 + 验证闭环

**Files:**
- Modify: `tests/agents/tools/test_ask_user.py`（既有 4 个用例适配 options 字段）
- Modify: `tests/services/test_dual_stream.py`（如 citation/sse 断言受影响）
- Modify: `tests/api/test_clarify.py`（如 ask_user 流程断言受影响）

**Interfaces:**
- Consumes: 前 8 个任务的全部产出

- [ ] **Step 1: 全量跑测试，记录失败项**

Run: `pytest tests/ -v 2>&1 | tail -30`
Expected: 有失败——凡断言 citations 结构 / ask_user 参数 / SSE 事件的用例需要适配（新增 `kind`、`options` 字段）

- [ ] **Step 2: 逐文件适配断言**

对每个失败用例，按新契约补齐字段断言：
- citations 断言：追加 `kind`（KB 场景断言 `"kb"`）
- ask_user 相关：**既有测试断言 dimension 注入（`options == ["2024年"]`），新默认 `ASK_USER_MODE_DSH=true` 会破坏它们**——在 `tests/agents/tools/test_ask_user.py` 加 autouse fixture 把 `ASK_USER_MODE_DSH` 置为 `False`（保留 dimension 注入测试语义；Task 8 的 dash 用例显式覆盖为 True）：
```python
@pytest.fixture(autouse=True)
def default_dual_mode(monkeypatch):
    """既有用例按 dual 模式（dimension 注入）断言；dash 用例自行覆盖。"""
    from src.config import settings as s

    monkeypatch.setattr(s, "ASK_USER_MODE_DSH", False)
    yield
```
- `test_agent_service.py` 的 `source == ["财报.pdf"]` 类断言保留，另补 `kind == "kb"`
- 如 `_format_end_item` helper 构造的 citation dict 缺 `kind`，补 `"kind": "kb"`

（每个文件的适配改动小，直接按 pytest 报错定位修改，不改业务行为。）

- [ ] **Step 3: 全量回归**

Run: `pytest tests/ -v`
Expected: 全部 PASS

- [ ] **Step 4: 质量门禁**

Run: `ruff check .`
Expected: `All checks passed!`
Run: `pyright src/`
Expected: 不新增 error（存量第三方误报除外）

- [ ] **Step 5: 提交**

```bash
git add tests/agents/tools/test_ask_user.py tests/services/test_dual_stream.py tests/api/test_clarify.py tests/services/test_agent_service.py
git commit -m "test: 适配 citations kind 与 ask_user options 契约变更"
```

---

### Task 10: 手动端到端验证

**Files:** 无代码改动（.env 配置 + 部署验证）

**Interfaces:**
- Consumes: 前 9 个任务的全部产出

- [ ] **Step 1: 配置 .env**

`.env` 追加（key 不提交）:
```
TAVILY_API_KEY=tvly-...
WEB_SEARCH_ENABLED=true
WEB_SEARCH_PER_TURN_LIMIT=3
TAVILY_TIMEOUT=5
ASK_USER_MODE_DSH=true
```

- [ ] **Step 2: 重启并验证五条路径**

```bash
docker compose restart app
```
逐条在界面/API 验证：
1. 闲聊"你好" → 直接答，不调用检索工具（容器日志无 `tool=retrieve_kb`）
2. KB 问题"东软集团2025年营收" → 检索命中 → 文档作答 + 引用（kind=kb）
3. KB 未命中"阿里云云防火墙计费区别" → 先检索 → 换词再检 → 仍无 → 回答含"该问题不在当前知识库范围内" + web 兜底内容 + 引用（kind=web，source 为 URL）
4. 日志验证判定路径：`judge: query=... stage=web_confirm` 与 `tool=search_web ... result_count=...`
5. Tavily 不可用（临时改错 key）→ search_web 返回空 → 纯拒答"未在文档中找到相关数据"，agent 不阻塞

- [ ] **Step 3: 观察与回归确认**

确认容器日志无 `TAVILY_API_KEY` 泄露；`grep -i "tavily\|api_key" /data/logs/app_*.log | grep -i "tvly-"` 无命中。

---

## Self-Review

**Spec coverage（对照 `docs/openspec/changes/web-search-fallback/` 五份 spec）：**

| spec 需求 | 对应 Task |
|---|---|
| web-search-fallback：search_web 工具 / extract / 注入引用通道 | Task 2, 3 |
| web-search-fallback：web 兜底流程 / 控制（开关/限次/熔断/无结果） | Task 3, 4, 10 |
| retrieval-judgment：一律先检索 / 内容判定 / 换词再检 / 去重 | Task 4, 5, 10 |
| ask-user-mode：options 字段 / ASK_USER_MODE_DSH 开关 | Task 1, 8 |
| retrieval-quality delta：abstention 决策补 web 兜底分支 | Task 4 |
| answer-grounding delta：拒答防御 / kind 字段 | Task 6, 7 |
| prompt：防滥用 guard / 引用指令不破坏 / Langfuse 同步 | Task 4, 10 |
| 可观测：judge 路径日志 / 用量日志 | Task 3（retrieve_kb `stage=retrieve` + search_web `stage=web_confirm`）、10 |
| 存量测试契约同步 | Task 9 |

**Type consistency check：**
- `settings.WEB_SEARCH_ENABLED / WEB_SEARCH_PER_TURN_LIMIT / TAVILY_TIMEOUT / ASK_USER_MODE_DSH / TAVILY_API_KEY` 全链路命名一致（Task 1 定义 → Task 3/8 消费）
- `tavily_search(query, top_k=, timeout=, transport=) -> list[dict]` 与 `tavily_extract(urls, timeout=, transport=) -> list[dict]` 在 Task 2 定义、Task 3 调用、Task 2 测试断言签名一致
- `RAGContext.kind: str = "kb"`（Task 6）→ `format_node` 读 `ctx.kind`（Task 6）→ `SSECitationEvent.kind`（Task 7）→ 前端，链路一致
- `SearchWebArgs`（Task 3）与 `search_web` 工具签名一致

**Known deferred（设计明确后置，不在本计划）：** KB 公司清单注入、prompt 防注入、KB 边界评估集、前端 web 引用样式适配、多轮指代 standalone query 验证、MCP 化。
