"""测试 delegate_task 工具 — inline 命中 / fork 命中 / 未知 skill / SSE delegate 事件推送。"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from src.agents.skills.delegate_task import DelegateTaskArgs, make_delegate_task
from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext, SkillRecord
from src.agents.skills.registry import SkillRegistry
from src.infra.llm.request_context import RequestContext, current_request_ctx


def _record(
    name: str, context: str, body: str, model: str | None = None
) -> SkillRecord:
    if context == SkillContext.INLINE:
        return SkillRecord(
            name=name,
            description=f"{name} 规则",
            context=context,
            inline_prompt=body,
            agent_prompt=None,
            model=model,
            source_path=Path(f"/tmp/{name}/SKILL.md"),
        )
    return SkillRecord(
        name=name,
        description=f"{name} 专家",
        context=context,
        inline_prompt=None,
        agent_prompt=body,
        model=model,
        source_path=Path(f"/tmp/{name}/SKILL.md"),
    )


class _FakeRegistry(SkillRegistry):
    """测试用 SkillRegistry 替身（避免真实目录，借类型以过 pyright）。"""

    def __init__(self, records: dict[str, SkillRecord]) -> None:
        super().__init__(MagicMock())  # loader 仅在 __init__ 赋值，本替身不使用
        self._records = records
        self.reload_calls = 0

    def reload_if_changed(self) -> None:
        self.reload_calls += 1

    def get(self, name: str) -> SkillRecord | None:
        return self._records.get(name)

    def names(self) -> list[str]:
        return sorted(self._records)

    def to_tool_description(self, max_chars: int = 500) -> str:
        lines = [f"{n}: {r.description}" for n, r in self._records.items()]
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 3] + "..."
        return text


def _event(kind, chunk=None, output=None):
    """构造 langgraph v2 事件 dict（fake astream_events 事件源元素）。"""
    data = {}
    if chunk is not None:
        data["chunk"] = chunk
    if output is not None:
        data["output"] = output
    return {
        "event": kind,
        "name": "agent",
        "metadata": {"langgraph_node": "agent"},
        "data": data,
    }


def _agen(*items):
    async def gen():
        for it in items:
            yield it

    return gen()


def _fake_sub_agent(*items):
    """astream_events 版 fake sub-agent（替换旧 fake_sub.ainvoke mock）。"""
    fake = MagicMock()
    fake.astream_events = lambda *a, **k: _agen(*items)
    return fake


def _drain_channel(ctx):
    """排空 ctx.clarify_channel，按序返回全部事件。"""
    out = []
    while not ctx.clarify_channel.empty():
        out.append(ctx.clarify_channel.get_nowait())
    return out


@pytest.mark.asyncio
async def test_inline_hit_returns_prompt_and_no_status():
    """inline 命中：返回渲染后方法论，不推 delegate start/end 事件（design D14）。"""
    rec = _record("finance-qa", SkillContext.INLINE, "请按规则作答：{task}")
    reg = _FakeRegistry({"finance-qa": rec})
    executor = SkillExecutor(main_llm=MagicMock())
    tool = make_delegate_task(reg, executor)

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        out = await tool.ainvoke({"task": "2024营收多少", "skill": "finance-qa"})
    finally:
        current_request_ctx.reset(token)

    assert "请按规则作答：2024营收多少" in out
    assert ctx.clarify_channel.empty()  # inline 不推状态


@pytest.mark.asyncio
async def test_fork_hit_pushes_delegate_start_end_with_id():
    """fork 命中：推 delegate start + end 事件（带 delegate_id；end ok=True/reason=''）。"""
    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    reg = _FakeRegistry({"finance-analyst": rec})
    executor = SkillExecutor(main_llm=MagicMock())
    tool = make_delegate_task(reg, executor)

    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="专家分析")),
        _event("on_chat_model_end", output=AIMessage(content="专家分析")),
    )

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch(
            "src.agents.skills.executor.create_react_agent", return_value=fake_sub
        ):
            out = await tool.ainvoke({"task": "分析年报", "skill": "finance-analyst"})
    finally:
        current_request_ctx.reset(token)

    assert "专家分析" in out
    items = _drain_channel(ctx)
    delegate_items = [it for it in items if it.get("type") == "delegate"]
    actions = [it["action"] for it in delegate_items]
    # 注意：executor 会在 content chunk 到达时 flush 出 kind=content 的 delta，
    # 故中间可能存在 delta——只断言首 start、末 end
    assert actions[0] == "start" and actions[-1] == "end"
    start = delegate_items[0]
    end = delegate_items[-1]
    assert start["delegate_id"] and end["delegate_id"] == start["delegate_id"]
    assert start["skill"] == "finance-analyst"
    assert end["ok"] is True and end["reason"] == ""
    # 旧 status 通道不再投递
    assert not [it for it in items if it.get("type") == "status"]


@pytest.mark.asyncio
async def test_fork_interrupted_end_carries_reason():
    """fork 中断（idle）：delegate end 携带 ok=False/reason=idle（区分"完成"）。"""
    import src.agents.skills.executor as exec_mod
    from src.config.const import SSEInteractionTexts

    async def _slow(*a, **k):
        yield _event("on_chat_model_stream", chunk=AIMessageChunk(content="a"))
        await asyncio.sleep(5)

    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    reg = _FakeRegistry({"finance-analyst": rec})
    executor = SkillExecutor(main_llm=MagicMock())
    tool = make_delegate_task(reg, executor)

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with (
            patch(
                "src.agents.skills.executor.create_react_agent",
                return_value=MagicMock(astream_events=_slow),
            ),
            patch.object(exec_mod.settings, "DELEGATE_MAX_IDLE_S", 0.1),
        ):
            out = await tool.ainvoke({"task": "分析年报", "skill": "finance-analyst"})
    finally:
        current_request_ctx.reset(token)

    assert out == SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
    items = _drain_channel(ctx)
    end = [it for it in items if it.get("action") == "end"][-1]
    assert end["ok"] is False and end["reason"] == "idle"


@pytest.mark.asyncio
async def test_delegate_task_description_lists_skills():
    """delegate_task.description 列出可用 skill（spec 2.1 动态描述）。"""
    rec = _record("finance-qa", SkillContext.INLINE, "方法论")
    reg = _FakeRegistry({"finance-qa": rec})
    tool = make_delegate_task(reg, SkillExecutor(main_llm=MagicMock()))
    assert "finance-qa" in tool.description


@pytest.mark.asyncio
async def test_unknown_skill_returns_available_list():
    """未知 skill：返回"skill 不存在"+ 可用列表。"""
    rec = _record("finance-qa", SkillContext.INLINE, "方法论")
    reg = _FakeRegistry({"finance-qa": rec})
    tool = make_delegate_task(reg, SkillExecutor(main_llm=MagicMock()))

    out = await tool.ainvoke({"task": "x", "skill": "missing-skill"})
    assert "skill 不存在" in out
    assert "finance-qa" in out


@pytest.mark.asyncio
async def test_delegate_task_registered_with_name_and_schema():
    """工具名为 delegate_task，入参 schema 含 task/skill。"""
    rec = _record("finance-qa", SkillContext.INLINE, "方法论")
    reg = _FakeRegistry({"finance-qa": rec})
    tool = make_delegate_task(reg, SkillExecutor(main_llm=MagicMock()))
    assert tool.name == "delegate_task"
    # @tool args_schema 注册的即 DelegateTaskArgs 模型，实例化验证 task/skill 字段
    assert tool.args_schema is DelegateTaskArgs
    args = DelegateTaskArgs(task="x", skill="finance-qa")
    assert args.task == "x"
    assert args.skill == "finance-qa"


def test_make_rag_tools_registers_delegate_when_provided(tmp_path):
    """make_rag_tools(delegate_task=...) 时工具列表含 delegate_task。"""
    from src.agents.skills.loader import SkillLoader
    from src.agents.tools.rag_tools import make_rag_tools

    d = tmp_path / "skills" / "finance-qa"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: finance-qa\ndescription: 财务问答\ncontext: inline\n---\n\n方法论",
        encoding="utf-8",
    )

    reg = SkillRegistry(SkillLoader(tmp_path / "skills"))
    reg.reload_if_changed()
    tool = make_delegate_task(reg, SkillExecutor(main_llm=MagicMock()))
    tools = make_rag_tools(
        MagicMock(), None, MagicMock(), MagicMock(), delegate_task=tool
    )
    names = [t.name for t in tools]
    assert "delegate_task" in names


def test_make_rag_tools_without_delegate_keeps_fixed_set():
    """未传 delegate_task → 工具列表保持既有集合（无 delegate_task）。"""
    from src.agents.tools.rag_tools import make_rag_tools

    tools = make_rag_tools(MagicMock(), None, MagicMock(), MagicMock())
    names = [t.name for t in tools]
    assert "delegate_task" not in names
