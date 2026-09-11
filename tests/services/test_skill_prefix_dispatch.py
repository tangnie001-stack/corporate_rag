"""`/xxx` 生成入口分派：plain / inline 单轮 / fork 直出 / 未知前缀。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.skills.models import SkillContext, SkillRecord
from src.config.const import SKILL_INJECTION_PREFIX
from src.services.agent_service import AgentService


def _record(
    name: str,
    context: str,
    *,
    user_invocable: bool = True,
    inline_prompt: str | None = None,
    fork_body: str | None = None,
) -> SkillRecord:
    """构造单个 SkillRecord。"""
    return SkillRecord(
        name=name,
        description="d",
        context=context,
        inline_prompt=inline_prompt,
        fork_body=fork_body,
        user_invocable=user_invocable,
        source_path=Path(f"/tmp/{name}/SKILL.md"),
    )


def _fork(name: str = "finance-analyst") -> SkillRecord:
    """构造 fork 技能。"""
    return _record(name, SkillContext.FORK, fork_body="任务：$ARGUMENTS")


def _inline(name: str = "finance-qa") -> SkillRecord:
    """构造 inline 技能。"""
    return _record(name, SkillContext.INLINE, inline_prompt="方法论 $ARGUMENTS")


def _make_service(
    records: list[SkillRecord],
    *,
    bound_agent: str = "",
    preset_system_prompt: str | None = None,
) -> tuple[AgentService, AsyncMock]:
    """构造只带分派所需依赖的 AgentService（跳过重型 __init__）。

    技能/预设注册表用 MagicMock 替身：user_visible()/model_visible() 返回
    真实记录，get() 按名解析，与生产 SkillRegistry 口径一致。
    """
    svc = AgentService.__new__(AgentService)
    chat_manager = AsyncMock()
    chat_manager.get_history_async.return_value = []
    chat_manager.get_session_agent_async.return_value = bound_agent
    chat_manager.add_message_async = AsyncMock()
    chat_manager.save_user_async = AsyncMock()
    chat_manager.bind_session_agent_async = AsyncMock(return_value=True)
    svc._chat_manager = chat_manager

    skill_registry = MagicMock()
    skill_registry.user_visible.return_value = [r for r in records if r.user_invocable]
    skill_registry.model_visible.return_value = [
        r for r in records if not r.disable_model_invocation
    ]
    by_name = {r.name: r for r in records}
    skill_registry.get.side_effect = lambda name: by_name.get(name)
    svc._skill_registry = skill_registry

    preset_registry = MagicMock()
    if preset_system_prompt is not None:
        preset = MagicMock()
        preset.system_prompt = preset_system_prompt
        preset_registry.get.return_value = preset
    else:
        preset_registry.get.return_value = None
    svc._preset_registry = preset_registry
    svc._graph = MagicMock()
    return svc, chat_manager


@pytest.mark.asyncio
async def test_plain_query_has_no_dispatch():
    """plain 文本 → direct_skill 为空、不写注入消息、写用户消息用原文。"""
    svc, chat_manager = _make_service([_fork()])
    _, launch_ctx = await svc.stream_chat("kb1", "s1", "帮我分析腾讯")

    assert launch_ctx["direct_skill"] == ""
    assert launch_ctx["query"] == "帮我分析腾讯"
    chat_manager.add_message_async.assert_awaited_once()
    assert chat_manager.add_message_async.await_args.args[2] == "帮我分析腾讯"


@pytest.mark.asyncio
async def test_inline_skill_injects_and_single_turn():
    """inline 技能 → 不设 direct_skill、query 变 task、持久化注入、history 追加注入条目。"""
    svc, chat_manager = _make_service([_inline()])
    _, launch_ctx = await svc.stream_chat("kb1", "s1", "/finance-qa 毛利率怎么算")

    assert launch_ctx["direct_skill"] == ""
    assert launch_ctx["query"] == "毛利率怎么算"
    # 两次写：一次用户原文（落库保留原文）、一次注入
    assert chat_manager.add_message_async.await_count == 2
    first = chat_manager.add_message_async.await_args_list[0]
    assert first.args[2] == "/finance-qa 毛利率怎么算"
    second = chat_manager.add_message_async.await_args_list[1]
    assert SKILL_INJECTION_PREFIX in second.args[2]
    # history 末尾是注入条目
    assert launch_ctx["history"][-1].content.startswith(SKILL_INJECTION_PREFIX)


@pytest.mark.asyncio
async def test_user_invocable_false_treated_as_unknown():
    """user-invocable:false 技能（只在 names() 不在 user_visible()）→ 按 unknown 处理，不注入。"""
    svc, chat_manager = _make_service(
        [
            _record(
                "secret-tool",
                SkillContext.INLINE,
                user_invocable=False,
                inline_prompt="方法论",
            )
        ]
    )
    _, launch_ctx = await svc.stream_chat("kb1", "s1", "/secret-tool xyz")

    assert launch_ctx["direct_skill"] == "secret-tool"
    chat_manager.add_message_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_fork_skill_sets_direct_skill():
    """fork 技能 → direct_skill = 技能名、query = task。"""
    svc, _ = _make_service([_fork()])
    _, launch_ctx = await svc.stream_chat("kb1", "s1", "/finance-analyst 腾讯2024")

    assert launch_ctx["direct_skill"] == "finance-analyst"
    assert launch_ctx["query"] == "腾讯2024"


@pytest.mark.asyncio
async def test_unknown_skill_sets_direct_skill():
    """未知技能 → direct_skill = 该名、query = task（走 skill_direct fail-open）。"""
    svc, _ = _make_service([_fork()])
    _, launch_ctx = await svc.stream_chat("kb1", "s1", "/ghost 任务")

    assert launch_ctx["direct_skill"] == "ghost"
    assert launch_ctx["query"] == "任务"


@pytest.mark.asyncio
async def test_ctx_persona_from_session_preset():
    """绑定会话智能体时 ctx.persona 取预设 system_prompt，has_skills = bool(model_visible())。"""
    svc, _ = _make_service(
        [_fork()], bound_agent="finance-expert", preset_system_prompt="我是财务专家"
    )
    _, launch_ctx = await svc.stream_chat("kb1", "s1", "你好")

    ctx = launch_ctx["ctx"]
    assert ctx.persona == "我是财务专家"
    assert ctx.has_skills is True
    assert ctx.known_skill_names == {"finance-analyst"}


@pytest.mark.asyncio
async def test_ctx_persona_empty_without_preset():
    """未绑定智能体 → ctx.persona 为空串。"""
    svc, _ = _make_service([_fork()])
    _, launch_ctx = await svc.stream_chat("kb1", "s1", "你好")

    ctx = launch_ctx["ctx"]
    assert ctx.persona == ""
    assert ctx.has_skills is True
