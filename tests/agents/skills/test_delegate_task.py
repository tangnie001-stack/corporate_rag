"""测试 delegate_task 工具 — inline 命中 / fork 命中 / 未知 skill / SSE 状态推送。"""

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


@pytest.mark.asyncio
async def test_inline_hit_returns_prompt_and_no_status():
    """inline 命中：返回渲染后方法论，不推 STAGE_DELEGATE 状态。"""
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
async def test_fork_hit_pushes_start_and_end_status():
    """fork 命中：推 start + end 两条 STAGE_DELEGATE 状态。"""
    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    reg = _FakeRegistry({"finance-analyst": rec})
    executor = SkillExecutor(main_llm=MagicMock())
    tool = make_delegate_task(reg, executor)

    async def _events(*args, **kwargs):
        yield {"event": "on_chat_model_start", "data": {}}
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": AIMessageChunk(content="专家分析")},
        }
        yield {
            "event": "on_chat_model_end",
            "data": {"output": AIMessage(content="专家分析")},
        }

    fake_sub = MagicMock()
    fake_sub.astream_events = _events

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
    phases = []
    while not ctx.clarify_channel.empty():
        item = ctx.clarify_channel.get_nowait()
        if item.get("type") == "status":
            assert item.get("stage") == "delegate"
            phases.append(item.get("phase"))
    assert phases == ["start", "end"]


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
