"""RequestContext.child() 隔离语义测试。"""

from src.infra.llm.request_context import RequestContext
from src.rag.context import RAGContext


def test_child_shares_channels_but_isolates_pools():
    """child 共享通道/取消信号，但引用池与计数全新。"""
    parent = RequestContext(
        session_id="s1", kb_id="k1", kb_bound=True, deep_thinking=True
    )
    parent.tool_contexts.append(
        RAGContext(content="内容", source="源", page=1, doc_id="d1", chunk_id="c1")
    )
    parent.temporal_years = [2024]
    parent.ask_count = 3

    child = parent.child()

    assert child.session_id == "s1"
    assert child.kb_id == "k1"
    assert child.kb_bound is True
    assert child.deep_thinking is True
    assert child.clarify_channel is parent.clarify_channel
    assert child.abort_signal is parent.abort_signal
    assert child.tool_contexts == []
    assert child.temporal_years == []
    assert child.ask_count == 0
    assert child.delegate_id == ""


def test_child_does_not_write_back():
    """写 child 不污染 parent（引用池隔离，D7）。"""
    parent = RequestContext(session_id="s1")
    child = parent.child()

    child.tool_contexts.append(
        RAGContext(content="内容", source="源", page=1, doc_id="d1", chunk_id="c1")
    )
    child.missing_years.append(2025)

    assert parent.tool_contexts == []
    assert parent.missing_years == []


def test_child_has_independent_abort_signal_identity():
    """共享的是同一个 Event 对象（取消必须贯通主流程）。"""
    parent = RequestContext(session_id="s1")
    parent.abort_signal.set()

    assert parent.child().abort_signal.is_set()
