"""verify 的判据材料必须随 AgentState 走，而不是读主 ctx。

常规轮由 agent_finalize 把主 ctx 池快照进 state；直出轮由直出节点把子代理池写进 state。
故"主 ctx 池"与"判据材料"必须解耦：state 有材料 → 护栏生效；state 无材料 → 即使主 ctx
有材料也不生效（证明不再偷看主 ctx）。
"""

import pytest

from src.agents.graph.state import AgentState
from src.agents.graph.verify.node import verify_node
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.context import RAGContext


class _KbContext(RAGContext):
    """最小 RAGContext 替身（只需 kind 供 _has_kb_context 判定）。"""

    def __init__(self, content: str = "2023 年营收 100 亿"):
        super().__init__(
            content=content,
            source="annual.pdf",
            page=12,
            doc_id="annual.pdf",
            chunk_id="annual.pdf:0",
            score=0.9,
            kind="kb",
        )

    def to_prompt_text(self) -> str:
        return self.content


def _state(**overrides) -> AgentState:
    """构造绑定 KB 的 state，判据材料缺省为空。"""
    defaults = {
        "session_id": "s1",
        "kb_id": "kb1",
        "query": "2024 年营收",
        "answer": "公司经营稳健，业务持续增长。",
        "tool_contexts": [],
        "verify_temporal_years": [],
    }
    defaults.update(overrides)
    return AgentState(**defaults)


@pytest.mark.asyncio
async def test_guardrail_uses_state_materials_not_main_ctx(monkeypatch):
    """直出轮形态：主 ctx 池为空、材料在 state → 护栏仍生效（不再空转）。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    main_ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    token = current_request_ctx.set(main_ctx)
    try:
        state = _state(tool_contexts=[_KbContext()])
        out = await verify_node(state)
    finally:
        current_request_ctx.reset(token)

    assert out["_needs_regenerate"] is True
    assert main_ctx.tool_contexts == []  # 主池不被改写（D7/D24）


@pytest.mark.asyncio
async def test_main_ctx_materials_are_not_read_any_more(monkeypatch):
    """反向证明：主 ctx 有材料但 state 没有 → 护栏不生效（判据只看 state）。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    main_ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    main_ctx.tool_contexts.append(_KbContext())
    token = current_request_ctx.set(main_ctx)
    try:
        state = _state(tool_contexts=[])
        out = await verify_node(state)
    finally:
        current_request_ctx.reset(token)

    assert out["_needs_regenerate"] is False


@pytest.mark.asyncio
async def test_year_check_uses_state_verify_temporal_years(monkeypatch):
    """年份完整性判据来自 state.verify_temporal_years（不是主 ctx）。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    main_ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    main_ctx.temporal_years = [2024]  # 主 ctx 有年份，但 state 没有 → 不做完整性校验
    token = current_request_ctx.set(main_ctx)
    try:
        state = _state(answer="2023 年营收 100 亿。", tool_contexts=[])
        out = await verify_node(state)
    finally:
        current_request_ctx.reset(token)

    assert out["_needs_regenerate"] is False
