"""测试 retrieve_kb 工具 — 全局递增引用编号 + collector 累积。

检索与精排均通过 monkeypatch mock，不发真实网络调用。
async 工具不支持 sync invoke（NotImplementedError），测试统一用 ainvoke。
"""

from typing import cast

import pytest

from src.agents.graph.state import AgentState
from src.agents.tools.rag_tools import make_rag_tools
from src.infra.db.vector_store import VectorStore
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag import retrieval
from src.rag.context import RAGContext


def _fixed_contexts() -> list[RAGContext]:
    """构造固定两条 RAGContext，作为 mock 检索/精排的返回。"""
    return [
        RAGContext(
            content="毛利率 40%",
            source="财报.pdf",
            page=1,
            doc_id="doc1",
            chunk_id="doc1:0",
            score=0.9,
        ),
        RAGContext(
            content="营收 100 亿",
            source="财报.pdf",
            page=2,
            doc_id="doc1",
            chunk_id="doc1:1",
            score=0.8,
        ),
    ]


@pytest.fixture
def retrieve_kb(monkeypatch):
    """工厂构建的 retrieve_kb 工具，search/rerank_results 已 mock 为固定返回两条上下文。"""

    async def fake_search(query, kb_id, vector_store, bm25):
        """mock search：返回空列表，结果由 fake_rerank 决定。"""
        return []

    def fake_rerank(query, results, reranker):
        """mock rerank_results：返回固定两条 RAGContext。"""
        return _fixed_contexts()

    monkeypatch.setattr(retrieval, "search", fake_search)
    monkeypatch.setattr(retrieval, "rerank_results", fake_rerank)

    # 依赖传 None：search/rerank_results 已被 mock，闭包内依赖不会被真实调用
    return make_rag_tools(
        vector_store=cast(VectorStore, None),
        bm25=None,
        reranker=None,
        prompt_manager=None,
    )[0]


def _new_state() -> AgentState:
    """构造带 _resolved_kb_ids 的初始 AgentState。"""
    state = AgentState.make_initial_state("s1", "kb1", "毛利率", [])
    state._resolved_kb_ids = ["kb1"]
    return state


@pytest.mark.asyncio
async def test_retrieve_kb_global_numbering(retrieve_kb):
    """连续两次调用，第二轮引用编号从 [3] 开始，与第一轮不冲突。"""
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        out1 = await retrieve_kb.ainvoke({"query": "毛利率", "state": _new_state()})
        out2 = await retrieve_kb.ainvoke({"query": "营收", "state": _new_state()})
    finally:
        current_request_ctx.reset(token)

    assert "[1]" in out1 and "[2]" in out1
    assert "[3]" in out2 and "[4]" in out2


@pytest.mark.asyncio
async def test_retrieve_kb_appends_to_collector(retrieve_kb):
    """调用后 current_request_ctx.get().tool_contexts 长度增加。"""
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        await retrieve_kb.ainvoke({"query": "毛利率", "state": _new_state()})
        ctx = current_request_ctx.get()
        assert ctx is not None
        assert len(ctx.tool_contexts) == 2
    finally:
        current_request_ctx.reset(token)
