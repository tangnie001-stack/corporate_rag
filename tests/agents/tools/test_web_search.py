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


async def _fake_tavily_search(query, top_k=5, timeout=5.0, transport=None):
    return [
        {"url": "https://a.com", "title": "A", "content": "内容A", "score": 0.9},
        {"url": "https://b.com", "title": "B", "content": "内容B", "score": 0.8},
    ]


async def _fake_tavily_extract(urls, timeout=5.0, transport=None):
    return [{"url": u, "content": f"{u} 正文"} for u in urls]


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

    out = await search_web.ainvoke({"query": "测试问题"})

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
    out = await search_web.ainvoke({"query": "测试"})
    assert "已达本轮联网搜索上限" in out
    assert ctx.web_count == 3
