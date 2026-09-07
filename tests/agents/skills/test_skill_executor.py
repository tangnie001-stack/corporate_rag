"""测试 SkillExecutor — inline 注入 / fork 子代理（零工具）/ 截断 / 超时。"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext, SkillRecord
from src.config.const import SSEInteractionTexts


def _record(**overrides) -> SkillRecord:
    """构造 SkillRecord，缺省 inline 形态。"""
    defaults = {
        "name": "test-skill",
        "description": "测试 skill",
        "context": SkillContext.INLINE,
        "inline_prompt": "方法论 {task}",
        "agent_prompt": None,
        "model": None,
        "thinking": None,
        "allowed_tools": [],
        "max_iterations": None,
        "source_path": Path("/tmp/test-skill/SKILL.md"),
    }
    defaults.update(overrides)
    return SkillRecord(**defaults)


@pytest.mark.asyncio
async def test_inline_returns_rendered_prompt():
    """inline：返回 inline_prompt，{task} 替换为任务文本。"""
    exe = SkillExecutor(main_llm=MagicMock())
    out = await exe.execute(_record(), task="计算毛利率")
    assert out == "方法论 计算毛利率"


@pytest.mark.asyncio
async def test_inline_without_placeholder_returns_as_is():
    """inline 无 {task} 占位：原样返回正文。"""
    rec = _record(inline_prompt="固定方法论，无需任务占位")
    exe = SkillExecutor(main_llm=MagicMock())
    out = await exe.execute(rec, task="任意任务")
    assert out == "固定方法论，无需任务占位"


@pytest.mark.asyncio
async def test_fork_reuses_main_llm_when_model_empty():
    """fork 且未声明 model：复用主 agent llm 实例（不新建）。"""
    main_llm = MagicMock()
    exe = SkillExecutor(main_llm=main_llm)
    rec = _record(
        context=SkillContext.FORK,
        agent_prompt="你是财务建模专家",
        inline_prompt=None,
        model=None,
    )

    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {
        "messages": [MagicMock(content="分析结果：营收下降 20%")]
    }

    with patch(
        "src.agents.skills.executor.create_react_agent", return_value=fake_sub
    ) as mock_create:
        out = await exe.execute(rec, task="分析年报")

    # 零工具硬保证：tools=[] 传入 create_react_agent
    args, kwargs = mock_create.call_args
    assert kwargs["tools"] == []
    assert kwargs["prompt"] == "你是财务建模专家"
    # 复用主 agent llm，未调 get_llm
    assert args[0] is main_llm
    assert "分析结果" in out


@pytest.mark.asyncio
async def test_fork_model_override_builds_new_llm():
    """fork 且声明 model：get_llm(model=record.model) 新建实例。"""
    fake_llm = MagicMock()
    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {"messages": [MagicMock(content="专家分析")]}

    with (
        patch(
            "src.agents.skills.executor.create_react_agent", return_value=fake_sub
        ) as mock_create,
        patch(
            "src.agents.skills.executor.get_llm", return_value=fake_llm
        ) as mock_get_llm,
    ):
        exe = SkillExecutor(main_llm=MagicMock())
        rec = _record(
            context=SkillContext.FORK,
            agent_prompt="专家人格",
            inline_prompt=None,
            model="qwen3.8-max",
        )
        out = await exe.execute(rec, task="分析")
        mock_get_llm.assert_called_once_with(model="qwen3.8-max")
        args, _kwargs = mock_create.call_args
        assert args[0] is fake_llm
        assert "专家分析" in out


@pytest.mark.asyncio
async def test_fork_result_truncated():
    """fork 结果超 DELEGATE_RESULT_LIMIT → 截断并带总字数提示。"""
    long_text = "字" * 3000
    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {"messages": [MagicMock(content=long_text)]}

    with patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub):
        exe = SkillExecutor(main_llm=MagicMock())
        rec = _record(
            context=SkillContext.FORK,
            agent_prompt="人格",
            inline_prompt=None,
            model=None,
        )
        out = await exe.execute(rec, task="分析")
        assert "3000" in out
        assert len(out) < 2000  # 截断后带前缀，远小于 3000
        assert out.startswith(SSEInteractionTexts.DELEGATE_TRUNCATED_PREFIX[:10])


@pytest.mark.asyncio
async def test_fork_timeout_returns_timeout_text():
    """fork 超时 → 返回 DELEGATE_TIMEOUT_TEXT（asyncio.wait_for 兜底）。"""
    from src.agents.skills import executor as exec_mod
    from src.config.const import DELEGATE_TIMEOUT

    async def _never(*args, **kwargs):
        await asyncio.sleep(DELEGATE_TIMEOUT + 1)
        return {"messages": []}

    fake_sub = AsyncMock()
    fake_sub.ainvoke.side_effect = _never

    with (
        patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub),
        patch.object(exec_mod, "DELEGATE_TIMEOUT", 0.01),
    ):
        exe = SkillExecutor(main_llm=MagicMock())
        rec = _record(
            context=SkillContext.FORK,
            agent_prompt="人格",
            inline_prompt=None,
            model=None,
        )
        out = await exe.execute(rec, task="分析")
        assert out == SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT


@pytest.mark.asyncio
async def test_fork_thinking_true_builds_llm_with_enable_thinking():
    """fork 且 thinking=True：新建 llm 时 extra_body.enable_thinking=True（D12 消费）。"""
    fake_llm = MagicMock()
    fake_llm.model_name = "main-model"
    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {"messages": [MagicMock(content="分析")]}

    with (
        patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub),
        patch(
            "src.agents.skills.executor.get_llm", return_value=fake_llm
        ) as mock_get_llm,
    ):
        main_llm = MagicMock()
        main_llm.model_name = "main-model"
        exe = SkillExecutor(main_llm=main_llm)
        rec = _record(
            context=SkillContext.FORK,
            agent_prompt="人格",
            inline_prompt=None,
            model=None,
            thinking=True,
        )
        await exe.execute(rec, task="分析")
        # model 空 → 继承主 agent model_name；thinking=True → extra_body enable_thinking
        kwargs = mock_get_llm.call_args.kwargs
        assert kwargs["model"] == "main-model"
        assert kwargs["extra_body"] == {"enable_thinking": True}


@pytest.mark.asyncio
async def test_fork_thinking_false_builds_llm_with_thinking_off():
    """fork 且 thinking=False：新建 llm 且 enable_thinking=False（显式关思考）。"""
    fake_llm = MagicMock()
    fake_llm.model_name = "main-model"
    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {"messages": [MagicMock(content="分析")]}

    with (
        patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub),
        patch(
            "src.agents.skills.executor.get_llm", return_value=fake_llm
        ) as mock_get_llm,
    ):
        main_llm = MagicMock()
        main_llm.model_name = "main-model"
        exe = SkillExecutor(main_llm=main_llm)
        rec = _record(
            context=SkillContext.FORK,
            agent_prompt="人格",
            inline_prompt=None,
            model=None,
            thinking=False,
        )
        await exe.execute(rec, task="分析")
        kwargs = mock_get_llm.call_args.kwargs
        assert kwargs["extra_body"] == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_fork_thinking_none_reuses_main_llm():
    """thinking 未声明（None）：不新建 llm，复用主 agent 实例（D12 跟随主 agent）。"""
    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {"messages": [MagicMock(content="分析")]}

    with (
        patch(
            "src.agents.skills.executor.create_react_agent", return_value=fake_sub
        ) as mock_create,
        patch("src.agents.skills.executor.get_llm") as mock_get_llm,
    ):
        main_llm = MagicMock()
        main_llm.model_name = "main-model"
        exe = SkillExecutor(main_llm=main_llm)
        rec = _record(
            context=SkillContext.FORK,
            agent_prompt="人格",
            inline_prompt=None,
            model=None,
            thinking=None,
        )
        out = await exe.execute(rec, task="分析")
        mock_get_llm.assert_not_called()
        args, _kwargs = mock_create.call_args
        assert args[0] is main_llm
        assert "分析" in out
