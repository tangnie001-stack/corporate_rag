"""测试 per-request contextvar 基建（RequestContext + current_request_ctx）。"""

import asyncio

from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.context import RAGContext


def test_contextvar_default_none():
    """未 set 前 current_request_ctx.get() 应为 None。"""
    assert current_request_ctx.get() is None


def test_set_and_reset():
    """set 后 get 返回同一实例，reset 后回到 None。"""
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    assert current_request_ctx.get() is ctx
    current_request_ctx.reset(token)
    assert current_request_ctx.get() is None


def test_request_context_fields():
    """RequestContext 字段齐全，且 tool_contexts 初始为独立空 list（两个实例互不影响）。"""
    ctx = RequestContext(session_id="s1")
    assert ctx.session_id == "s1"
    assert isinstance(ctx.clarify_channel, asyncio.Queue)
    assert isinstance(ctx.abort_signal, asyncio.Event)
    assert ctx.registry == {}
    assert ctx.tool_contexts == []
    assert ctx.ask_count == 0

    ctx2 = RequestContext(session_id="s2")
    rag_ctx = RAGContext(
        content="内容", source="源", page=1, doc_id="d1", chunk_id="c1"
    )
    ctx.tool_contexts.append(rag_ctx)
    assert ctx.tool_contexts == [rag_ctx]
    assert ctx2.tool_contexts == []
