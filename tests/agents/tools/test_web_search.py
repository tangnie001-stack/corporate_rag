"""search_web 工具测试：mock tavily 客户端与 RequestContext，验证编号/限次/多查询合并。"""

import pytest

from src.agents.tools import web_tools
from src.agents.tools.web_tools import SearchWebArgs, search_web
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.context import RAGContext


@pytest.fixture
def ctx():
    """构造并 set 一个干净的 RequestContext，测试后清理。"""
    c = RequestContext(session_id="s1")
    token = current_request_ctx.set(c)
    yield c
    current_request_ctx.reset(token)


async def _fake_tavily_search(query, top_k=5, timeout=5.0, transport=None):
    return [
        {"url": "https://a.com", "title": "A", "content": "内容A", "score": 0.9},
        {"url": "https://b.com", "title": "B", "content": "内容B", "score": 0.8},
    ]


async def _fake_tavily_extract(urls, timeout=5.0, transport=None):
    return [{"url": u, "content": f"{u} 正文"} for u in urls]


def test_search_web_args_schema():
    """SearchWebArgs 支持多查询列表（queries），top_k 默认 5。"""
    args = SearchWebArgs(queries=["腾讯 2023 年报 业绩", "腾讯 2025 年 业绩"])
    assert len(args.queries) == 2
    assert args.top_k == 5


@pytest.mark.asyncio
async def test_search_web_appends_with_global_numbering(monkeypatch, ctx):
    """结果按全局递增编号追加进 tool_contexts，source=URL。"""
    monkeypatch.setattr(web_tools, "tavily_search", _fake_tavily_search)
    monkeypatch.setattr(web_tools, "tavily_extract", _fake_tavily_extract)
    # 预置一个 retrieve_kb 的上下文（模拟先检索过），验证编号从 2 开始
    ctx.tool_contexts.append(
        RAGContext(
            content="kb内容", source="doc.pdf", page=1, doc_id="d1", chunk_id="c1"
        )
    )

    out = await search_web.ainvoke({"queries": ["测试问题"]})

    assert out.startswith("[2] 来源: https://a.com")
    assert len(ctx.tool_contexts) == 3
    web_ctx = ctx.tool_contexts[-1]
    assert web_ctx.source == "https://b.com"
    assert web_ctx.kind == "web"


@pytest.mark.asyncio
async def test_search_web_per_turn_limit(monkeypatch, ctx):
    """达每轮限次后返回限次提示，不再调用 tavily。"""
    ctx.web_count = 3  # WEB_SEARCH_PER_TURN_LIMIT 默认 3
    monkeypatch.setattr(
        web_tools,
        "tavily_search",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应被调用")),
    )
    out = await search_web.ainvoke({"queries": ["测试"]})
    assert "已达本轮联网搜索上限" in out
    assert ctx.web_count == 3


@pytest.mark.asyncio
async def test_search_web_multiquery_uses_one_quota(monkeypatch, ctx):
    """多查询一次调用：并行搜索、按查询顺序合并统一编号，web_count 只 +1。"""

    async def _query_search(query, top_k=5, timeout=5.0, transport=None):
        return [
            {
                "url": f"https://{query}.com/1",
                "title": f"{query}1",
                "content": f"{query}内容1",
                "score": 0.9,
            },
            {
                "url": f"https://{query}.com/2",
                "title": f"{query}2",
                "content": f"{query}内容2",
                "score": 0.8,
            },
            {
                "url": f"https://{query}.com/3",
                "title": f"{query}3",
                "content": f"{query}内容3",
                "score": 0.7,
            },
        ]

    extract_calls: list[list[str]] = []

    async def _recording_extract(urls, timeout=5.0, transport=None):
        extract_calls.append(urls)
        return [{"url": u, "content": f"{u} 正文"} for u in urls]

    monkeypatch.setattr(web_tools, "tavily_search", _query_search)
    monkeypatch.setattr(web_tools, "tavily_extract", _recording_extract)

    out = await search_web.ainvoke({"queries": ["q1", "q2"]})

    # 一次多查询调用只占 1 次额度
    assert ctx.web_count == 1
    # 各 query 结果按查询顺序合并，编号连续 [1]~[6]
    assert out.startswith("[1] 来源: https://q1.com/1\n内容: https://q1.com/1 正文")
    assert "[4] 来源: https://q2.com/1" in out
    assert "[6] 来源: https://q2.com/3" in out
    assert len(ctx.tool_contexts) == 6
    # extract 只拉每个 query 的 top-1~2，且合并为一次 extract 调用
    assert extract_calls == [
        ["https://q1.com/1", "https://q1.com/2", "https://q2.com/1", "https://q2.com/2"]
    ]


@pytest.mark.asyncio
async def test_search_web_empty_queries(monkeypatch, ctx):
    """空查询列表防御：直接返回空串，不占额度、不调 tavily。"""

    async def _should_not_search(*a, **k):
        raise AssertionError("不应调用 tavily_search")

    monkeypatch.setattr(web_tools, "tavily_search", _should_not_search)

    out = await search_web.ainvoke({"queries": []})

    assert out == ""
    assert ctx.web_count == 0


@pytest.mark.asyncio
async def test_search_web_filters_blank_queries(monkeypatch, ctx):
    """空串/纯空白查询被过滤，不调 tavily 搜索空白 query，仅占 1 次额度。"""

    searched = []

    async def _recording_search(query, top_k=5, timeout=5.0, transport=None):
        searched.append(query)
        return []

    monkeypatch.setattr(web_tools, "tavily_search", _recording_search)

    out = await search_web.ainvoke({"queries": ["", "  ", "real"]})

    assert out == ""
    assert searched == ["real"]
    assert ctx.web_count == 1


@pytest.mark.asyncio
async def test_search_web_partial_failure_keeps_success(monkeypatch, ctx):
    """单个 query 失败不连坐全部：异常项被过滤，成功项正常合并进结果。"""

    async def _flaky_search(query, top_k=5, timeout=5.0, transport=None):
        if query == "bad":
            raise RuntimeError("tavily down")
        return [
            {
                "url": f"https://{query}.com/1",
                "title": query,
                "content": f"{query}内容",
                "score": 0.9,
            }
        ]

    async def _extract(urls, timeout=5.0, transport=None):
        return [{"url": u, "content": f"{u} 正文"} for u in urls]

    monkeypatch.setattr(web_tools, "tavily_search", _flaky_search)
    monkeypatch.setattr(web_tools, "tavily_extract", _extract)

    out = await search_web.ainvoke({"queries": ["bad", "good"]})

    assert "[1] 来源: https://good.com/1" in out
    assert "bad" not in out
    assert len(ctx.tool_contexts) == 1


@pytest.mark.asyncio
async def test_search_web_all_failures_returns_empty(monkeypatch, ctx):
    """全部 query 失败：过滤后无成功项，返回空串（不抛异常）。"""

    async def _fail_all(query, top_k=5, timeout=5.0, transport=None):
        raise RuntimeError("tavily down")

    monkeypatch.setattr(web_tools, "tavily_search", _fail_all)

    out = await search_web.ainvoke({"queries": ["a", "b"]})

    assert out == ""
    assert ctx.web_count == 1


@pytest.mark.asyncio
async def test_search_web_verify_guided_no_to_web_signal(monkeypatch, ctx):
    """verify 指派联网（web_guided=True）→ 不产 to_web 缺陷信号。"""
    captured: dict = {}

    def _fake_signal(signal, query, iteration, **fields):
        """mock retrieval_signal：捕获调用参数。"""
        captured["signal"] = signal

    monkeypatch.setattr("src.core.logging.retrieval_signal", _fake_signal)
    monkeypatch.setattr(web_tools, "tavily_search", _fake_tavily_search)
    monkeypatch.setattr(web_tools, "tavily_extract", _fake_tavily_extract)
    ctx.kb_id = "kb1"
    ctx.kb_bound = True
    ctx.web_guided = True  # verify 已指派联网

    out = await search_web.ainvoke({"queries": ["腾讯2023年报"]})

    assert out.startswith("[1] 来源: https://a.com")
    assert "signal" not in captured  # verify 指派联网不产 to_web


@pytest.mark.asyncio
async def test_search_web_autonomous_emits_to_web_signal(monkeypatch, ctx):
    """agent 自主降级调 search_web（web_guided=False）→ 产 to_web 缺陷信号。"""
    captured: dict = {}

    def _fake_signal(signal, query, iteration, **fields):
        """mock retrieval_signal：捕获调用参数。"""
        captured["signal"] = signal
        captured["fields"] = fields

    monkeypatch.setattr("src.core.logging.retrieval_signal", _fake_signal)
    monkeypatch.setattr(web_tools, "tavily_search", _fake_tavily_search)
    monkeypatch.setattr(web_tools, "tavily_extract", _fake_tavily_extract)
    ctx.kb_id = "kb1"
    ctx.kb_bound = True  # 态 B：绑定 KB；web_guided 默认 False（自主降级）

    await search_web.ainvoke({"queries": ["腾讯2023年报"]})

    assert captured.get("signal") == "to_web"
    assert captured["fields"]["kb_id"] == "kb1"
    assert captured["fields"]["result_count"] > 0
    assert "latency_ms" in captured["fields"]
