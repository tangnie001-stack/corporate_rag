"""直出轮整链：主 agent 零 LLM 轮、子代理材料进 state、引用不丢。"""

from unittest.mock import MagicMock

import pytest

from src.agents.graph.skill_direct import make_skill_direct_node
from src.agents.graph.state import AgentState
from src.agents.graph.workflow import build_graph
from src.agents.skills.models import SkillContext, SkillRecord
from src.config.const import SSEInteractionTexts
from src.infra.llm.request_context import RequestContext, current_request_ctx


class _KbContext:
    """最小 RAGContext 替身。"""

    def __init__(self, content: str, page: int):
        self.content = content
        self.source = "annual.pdf"
        self.page = page
        self.score = 0.9
        self.kind = "kb"
        self.tier = ""

    def to_prompt_text(self) -> str:
        return self.content


class _FakeRegistry:
    """只实现直出节点用到的 get / reload。"""

    def __init__(self, record):
        self._record = record

    def reload_if_changed(self) -> None:
        return None

    def get(self, name):
        if name == self._record.name:
            return self._record
        return None

    def user_visible(self):
        if self._record.user_invocable:
            return [self._record]
        return []


class _FakeExecutor:
    """替身：在子池写 2 条材料并返回带 [1][2] 的答案。"""

    def __init__(self):
        self.seen_run = None

    async def execute(self, record, task, run):
        self.seen_run = run
        run.ctx.tool_contexts.append(_KbContext("2024 年营收 1000 亿", 12))
        run.ctx.tool_contexts.append(_KbContext("2023 年营收 900 亿", 13))
        run.ctx.temporal_years.append(2024)
        return "公司 2024 年营收 1000 亿[1]，同比增至 900 亿[2]。"


class _StubPromptManager:
    """极简 PromptManager 替身（agent 节点直出轮不会执行，仅满足构建签名）。"""

    def get_system_prompt(self):
        """返回固定系统指令。"""
        return "system prompt"

    def get_user_template(self, context="", query=""):
        """返回含 query 的用户模板。"""
        return f"user template: {query}"


def _graph(fake_executor, record, **kwargs):
    """构建带直出节点的最小图（tools 传空避免真实检索）。"""
    node = make_skill_direct_node(_FakeRegistry(record), fake_executor)
    return build_graph(
        vector_store=MagicMock(),
        bm25=None,
        llm=MagicMock(),
        reranker=MagicMock(),
        prompt_manager=_StubPromptManager(),
        tools=[],
        skill_direct_node=node,
        **kwargs,
    )


async def _run_updates(graph, initial_state):
    """以 updates 模式累积节点返回 dict 与节点顺序（跳过 __start__/__end__ 哨兵）。"""
    # 以初始 state 的迭代计数为基线：agent 从未运行时该字段不会被任何节点返回，
    # 但终端断言仍需它（主 agent 零 LLM 轮 → 恒为初始值 0）
    final: dict = {"_agent_iterations": initial_state._agent_iterations}
    node_order: list[str] = []
    async for update in graph.astream(initial_state, stream_mode="updates"):
        for name, payload in update.items():
            if name.startswith("__"):
                continue
            node_order.append(name)
            final.update(payload)
    return final, node_order


@pytest.mark.asyncio
async def test_direct_round_keeps_child_citations_and_zero_agent_rounds():
    """直出轮：主 agent 0 轮、citations 落在子代理池、主池不被污染。"""
    record = SkillRecord(
        name="finance-analyst",
        description="d",
        context=SkillContext.FORK,
        fork_body="任务：$ARGUMENTS",
        allowed_tools=[],
    )
    fake = _FakeExecutor()
    graph = _graph(fake, record)
    main_ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    token = current_request_ctx.set(main_ctx)
    try:
        final, node_order = await _run_updates(
            graph,
            AgentState(
                session_id="s1",
                kb_id="kb1",
                query="2024 年营收",
                direct_skill=record.name,
            ),
        )
    finally:
        current_request_ctx.reset(token)

    assert "agent" not in node_order  # 主 agent 一轮都没跑
    assert final["_agent_iterations"] == 0
    assert [c["index"] for c in final["citations"]] == [1, 2]
    assert final["answer"].startswith("公司 2024 年营收")
    assert final["verify_temporal_years"] == [2024]  # 子 ctx 年份传播进 state
    assert main_ctx.tool_contexts == []  # 主池保持为空（D7/D24）
    assert main_ctx.temporal_years == []


@pytest.mark.asyncio
async def test_inline_skill_falls_open():
    """direct_skill 命中非 fork（INLINE）→ 兜底文案，不抛、不空转。"""
    record = SkillRecord(
        name="finance-qa",
        description="d",
        context=SkillContext.INLINE,
        inline_prompt="方法论 $ARGUMENTS",
    )
    graph = _graph(_FakeExecutor(), record)
    main_ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    token = current_request_ctx.set(main_ctx)
    try:
        final, _node_order = await _run_updates(
            graph,
            AgentState(
                session_id="s1", kb_id="kb1", query="q", direct_skill="finance-qa"
            ),
        )
    finally:
        current_request_ctx.reset(token)

    assert final["_needs_regenerate"] is False
    assert final["citations"] == []


@pytest.mark.asyncio
async def test_unknown_skill_falls_open():
    """direct_skill 查不到（registry.get → None）→ 兜底文案，不抛、不空转、无引用。"""
    record = SkillRecord(
        name="finance-analyst",
        description="d",
        context=SkillContext.FORK,
        fork_body="任务：$ARGUMENTS",
        allowed_tools=[],
    )
    graph = _graph(_FakeExecutor(), record)
    main_ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    token = current_request_ctx.set(main_ctx)
    try:
        final, _node_order = await _run_updates(
            graph,
            AgentState(
                session_id="s1",
                kb_id="kb1",
                query="q",
                direct_skill="no-such-skill",
            ),
        )
    finally:
        current_request_ctx.reset(token)

    assert final["answer"] == SSEInteractionTexts.UNKNOWN_SKILL_PREFIX.format(
        skill="no-such-skill", available="finance-analyst"
    )
    assert final["_needs_regenerate"] is False
    assert final["citations"] == []


@pytest.mark.asyncio
async def test_disabled_fork_skill_treated_as_disabled():
    """user-invocable:false 的 fork 技能 → 不执行子代理，返回 SKILL_USER_DISABLED。"""
    record = SkillRecord(
        name="finance-analyst",
        description="d",
        context=SkillContext.FORK,
        fork_body="任务：$ARGUMENTS",
        allowed_tools=[],
        user_invocable=False,
    )
    fake = _FakeExecutor()
    graph = _graph(fake, record)
    main_ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    token = current_request_ctx.set(main_ctx)
    try:
        final, node_order = await _run_updates(
            graph,
            AgentState(
                session_id="s1",
                kb_id="kb1",
                query="q",
                direct_skill=record.name,
            ),
        )
    finally:
        current_request_ctx.reset(token)

    assert fake.seen_run is None  # executor.execute 未被调用
    assert "agent" not in node_order
    assert final["answer"] == SSEInteractionTexts.SKILL_USER_DISABLED.format(
        skill="finance-analyst"
    )
    assert final["_needs_regenerate"] is False
    assert final["citations"] == []


@pytest.mark.asyncio
async def test_disabled_inline_skill_treated_as_disabled():
    """user-invocable:false 的 inline 技能 → 返回 SKILL_USER_DISABLED（非 SKILL_DIRECT_UNAVAILABLE）。"""
    record = SkillRecord(
        name="finance-qa",
        description="d",
        context=SkillContext.INLINE,
        inline_prompt="方法论 $ARGUMENTS",
        user_invocable=False,
    )
    graph = _graph(_FakeExecutor(), record)
    main_ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    token = current_request_ctx.set(main_ctx)
    try:
        final, _node_order = await _run_updates(
            graph,
            AgentState(
                session_id="s1", kb_id="kb1", query="q", direct_skill="finance-qa"
            ),
        )
    finally:
        current_request_ctx.reset(token)

    assert final["answer"] == SSEInteractionTexts.SKILL_USER_DISABLED.format(
        skill="finance-qa"
    )
    assert final["_needs_regenerate"] is False
    assert final["citations"] == []


@pytest.mark.asyncio
async def test_missing_ctx_falls_open():
    """current_request_ctx 未设置 → 上下文兜底文案，不抛。"""
    record = SkillRecord(
        name="finance-analyst",
        description="d",
        context=SkillContext.FORK,
        fork_body="任务：$ARGUMENTS",
        allowed_tools=[],
    )
    graph = _graph(_FakeExecutor(), record)
    token = current_request_ctx.set(None)
    try:
        final, _node_order = await _run_updates(
            graph,
            AgentState(
                session_id="s1", kb_id="kb1", query="q", direct_skill=record.name
            ),
        )
    finally:
        current_request_ctx.reset(token)

    assert final["answer"] == SSEInteractionTexts.SKILL_DIRECT_CTX_UNAVAILABLE
    assert final["_needs_regenerate"] is False
