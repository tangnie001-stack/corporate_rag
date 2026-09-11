"""fork 子代理构造契约：system_prompt=人设、user message=skill 正文、tools=交集、middleware 空。"""

import pytest

from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext, SkillRecord
from src.config.prompts import FORK_DEFAULT_EXECUTOR_PROMPT, FORK_EXECUTION_CONTRACT


class _FakeAgent:
    pass


@pytest.mark.asyncio
async def test_sub_agent_uses_system_prompt_and_user_body(monkeypatch):
    """_build_sub_agent 走 create_agent：system=人设、tools=交集、middleware=[]。"""
    captured = {}

    def _fake_create_agent(
        model, tools=None, system_prompt=None, middleware=None, **kw
    ):
        captured["tools"] = tools
        captured["system_prompt"] = system_prompt
        captured["middleware"] = middleware
        return _FakeAgent()

    monkeypatch.setattr("src.agents.skills.executor.create_agent", _fake_create_agent)
    executor = SkillExecutor(main_llm=object(), tool_provider=list)
    record = SkillRecord(
        name="finance-analyst",
        description="d",
        context=SkillContext.FORK,
        fork_body="按方法论分析。\n任务：$ARGUMENTS",
        allowed_tools=["retrieve_kb"],
    )
    executor._build_sub_agent(record, preset=None)

    assert captured["system_prompt"]  # 执行者人设非空（无 preset → 系统默认人设）
    assert captured["tools"] == []  # available 为空 → 交集为空
    assert captured["middleware"] == []


def test_render_fork_task_replaces_declared_placeholder():
    """正文声明占位符：直接渲染任务，不追加默认段。"""
    executor = SkillExecutor(main_llm=object())
    record = SkillRecord(
        name="s",
        description="d",
        context=SkillContext.FORK,
        fork_body="按方法论分析。\n任务：$ARGUMENTS",
    )
    out = executor._render_fork_task(record, "腾讯2024")
    assert out == "按方法论分析。\n任务：腾讯2024"


def test_render_fork_task_appends_when_no_placeholder():
    """正文未声明占位符：末尾追加默认任务段（FORK_TASK_APPEND_TMPL）。"""
    from src.config.prompts import FORK_TASK_APPEND_TMPL

    executor = SkillExecutor(main_llm=object())
    record = SkillRecord(
        name="s",
        description="d",
        context=SkillContext.FORK,
        fork_body="只按方法论作答。",
    )
    out = executor._render_fork_task(record, "腾讯2024")
    expected = "只按方法论作答。\n" + FORK_TASK_APPEND_TMPL.format(task="腾讯2024")
    assert out == expected


def test_executor_system_prompt_falls_back_to_default():
    """无 preset：返回系统默认执行者人设 + 执行契约。"""
    executor = SkillExecutor(main_llm=object())
    assert executor._executor_system_prompt(None) == (
        FORK_DEFAULT_EXECUTOR_PROMPT + FORK_EXECUTION_CONTRACT
    )


def test_executor_system_prompt_uses_preset_persona():
    """有 preset：返回 preset.system_prompt + 执行契约。"""

    class _Preset:
        system_prompt = "你是一名资深财务分析师。"

    executor = SkillExecutor(main_llm=object())
    assert executor._executor_system_prompt(_Preset()) == (
        "你是一名资深财务分析师。" + FORK_EXECUTION_CONTRACT
    )
