"""RequestContext 的领域与工具集字段，以及子上下文复制。"""

from src.infra.llm.request_context import RequestContext


def test_child_copies_kb_domain_and_tool_names() -> None:
    """child() 必须复制两个字段，否则 fork 子代理丢失领域视角与工具集判据。"""
    parent = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    parent.kb_domain = "finance"
    parent.tool_names = frozenset({"retrieve_kb", "search_web"})

    child = parent.child()

    assert child.kb_domain == "finance"
    assert child.tool_names == frozenset({"retrieve_kb", "search_web"})
    assert child.kb_id == "kb1"
    assert child.kb_bound is True


def test_defaults_are_general_and_empty_tools() -> None:
    """默认值为保留值 general 与空集（无工具）。"""
    ctx = RequestContext(session_id="s1")
    assert ctx.kb_domain == "general"
    assert ctx.tool_names == frozenset()
