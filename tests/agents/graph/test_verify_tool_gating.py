"""verify 运行期指引的工具集条件渲染：标记不变量 + 联网工具缺失时不询问。"""

import pytest

from src.config.const import VERIFY_GUIDANCE_MARKER, VERIFY_HINT_MARKER
from src.config.prompts import VERIFY_GUIDANCE_PROMPT, VERIFY_HINT_PROMPT


def _tools(*names: str) -> frozenset[str]:
    """构造工具名集合。"""
    return frozenset(names)


def test_rendered_guidance_always_contains_marker() -> None:
    """任何渲染产物只要含指引正文，就必须含对应查重标记（否则查重恒假、每轮重复注入）。"""
    rendered = VERIFY_GUIDANCE_PROMPT.format(
        missing=[2023], marker=VERIFY_GUIDANCE_MARKER
    )
    assert VERIFY_GUIDANCE_MARKER in rendered
    assert "search_web" in rendered

    hint = VERIFY_HINT_PROMPT.format(missing=[2023], marker=VERIFY_HINT_MARKER)
    assert VERIFY_HINT_MARKER in hint


def test_marker_coupling_wired_via_placeholder() -> None:
    """常量与文案的耦合是硬约束：模板以 {marker} 占位符承载查重短语。

    P1 段模型化后查重短语不再硬编码于模板正文，而由调用方经 const.*_MARKER 注入；
    耦合点是占位符，渲染产物含 marker 由 test_rendered_guidance_always_contains_marker 验证。
    """
    assert "{marker}" in VERIFY_GUIDANCE_PROMPT
    assert "{marker}" in VERIFY_HINT_PROMPT


@pytest.mark.asyncio
async def test_no_web_ask_when_search_web_absent(monkeypatch) -> None:
    """联网工具未注册时不得向用户询问联网，直接走标注直通。"""
    from src.agents.graph.state import AgentState
    from src.agents.graph.verify import regen_decision
    from src.infra.llm.request_context import RequestContext

    asked: list[list[int]] = []

    async def fake_ask(state, missing):
        """记录询问并返回 True（若被调用即为违规）。"""
        asked.append(missing)
        return True

    monkeypatch.setattr(regen_decision.ask_confirm, "_ask_web_confirm", fake_ask)

    state = AgentState(session_id="s1", kb_id="kb1", query="2023 年营收？")
    ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    ctx.tool_names = _tools("retrieve_kb", "ask_user")

    result = await regen_decision.decide_missing_web(
        state, ctx, required=[2022, 2023], missing=[2023], answer="答案"
    )

    assert asked == [], "search_web 未注册时不得询问"
    assert result["_needs_regenerate"] is False
    assert "未联网补充" in result["answer"]
    assert ctx.verify_ask_count == 0, "未询问则不应消耗询问计数"


@pytest.mark.asyncio
async def test_web_ask_happens_when_search_web_registered(monkeypatch) -> None:
    """联网工具已注册时照常询问。"""
    from src.agents.graph.state import AgentState
    from src.agents.graph.verify import regen_decision
    from src.infra.llm.request_context import RequestContext

    asked: list[list[int]] = []

    async def fake_ask(state, missing):
        """记录询问并确认联网。"""
        asked.append(missing)
        return True

    monkeypatch.setattr(regen_decision.ask_confirm, "_ask_web_confirm", fake_ask)

    state = AgentState(session_id="s1", kb_id="kb1", query="2023 年营收？")
    ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    ctx.tool_names = _tools("retrieve_kb", "search_web")

    await regen_decision.decide_missing_web(
        state, ctx, required=[2022, 2023], missing=[2023], answer="答案"
    )

    assert asked == [[2023]]
