"""测试 SkillExecutor — inline 注入 / fork 子代理（零工具）/ 截断 / 超时。"""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

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


# ---- fork 回调隔离回归（final review Critical） ----

_LEAK_MARKER = "子代理分析泄漏标记XYZ"
_FORK_TASK = "委派给子代理的任务"
_FORK_ANSWER = f"【子代理分析】{_LEAK_MARKER}"
_FINAL_ANSWER = "整合后的最终回答，不重复子代理原文"


class _ScenarioChatModel(BaseChatModel):
    """按输入消息特征分场景的假 LLM（真实 on_chat_model_* 事件源）。

    - 外层首轮（无 SystemMessage/ToolMessage）→ 流式输出 delegate_task 工具调用
    - fork 子代理内（含 SystemMessage prompt）→ 流式输出 _FORK_ANSWER（含泄漏标记）
    - 外层收尾（含 ToolMessage）→ 流式输出 _FINAL_ANSWER
    """

    model_name: str = "scenario-fake"

    def _pick_text(self, messages) -> str:
        """按消息特征选应答文本：fork 内带 prompt SystemMessage，收尾带 ToolMessage。"""
        if any(isinstance(m, SystemMessage) for m in messages):
            return _FORK_ANSWER
        if any(isinstance(m, ToolMessage) for m in messages):
            return _FINAL_ANSWER
        return ""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        text = self._pick_text(messages)
        if text:
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=text))]
            )
        ai_message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "delegate_task",
                    "args": {"task": _FORK_TASK, "skill": "analyst"},
                    "id": "call_delegate_1",
                    "type": "tool_call",
                }
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    def _stream(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> Iterator[ChatGenerationChunk]:
        text = self._pick_text(messages)
        if not text:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "delegate_task",
                            "args": json.dumps(
                                {"task": _FORK_TASK, "skill": "analyst"}
                            ),
                            "id": "call_delegate_1",
                            "index": 0,
                        }
                    ],
                )
            )
            return
        for ch in text:
            yield ChatGenerationChunk(message=AIMessageChunk(content=ch))

    @property
    def _llm_type(self) -> str:
        return "scenario-chat-model"


def _make_fork_delegate_tool(model: BaseChatModel):
    """构造走真实 fork 路径的 delegate_task 工具（SkillExecutor 不 mock create_react_agent）。"""

    @tool("delegate_task")
    async def delegate_task(task: str, skill: str) -> str:
        """调用 fork skill 深度分析并返回子代理文本。"""
        record = _record(
            context=SkillContext.FORK,
            agent_prompt="你是领域分析专家",
            inline_prompt=None,
            model=None,
            thinking=None,
        )
        executor = SkillExecutor(main_llm=model)
        return await executor.execute(record, task)

    return delegate_task


def _collect_fork_leak_events(stream_events) -> tuple[str, list[str]]:
    """从外层 astream_events 累积 full_answer 并收集带泄漏标记的 token 事件。

    与 agent_service._convert_event 同口径：仅 metadata.langgraph_node == "agent"
    的 on_chat_model_stream 文本进 full_answer（对应外层 SSE token 累积）。

    Args:
        stream_events: graph.astream_events(v2) 的完整事件迭代结果

    Returns:
        (full_answer, leaked_tokens)：full_answer 为外层 agent 节点累积文本；
        leaked_tokens 为事件流中出现泄漏标记的 content 片段（空 = 无泄漏）
    """
    full_answer = ""
    leaked_tokens = []
    for event in stream_events:
        if event.get("event") != "on_chat_model_stream":
            continue
        chunk = (event.get("data") or {}).get("chunk")
        content = getattr(chunk, "content", "") or ""
        if not content:
            continue
        metadata = event.get("metadata") or {}
        if metadata.get("langgraph_node") == "agent":
            full_answer += content
        if _LEAK_MARKER in content:
            leaked_tokens.append(content)
    return full_answer, leaked_tokens


@pytest.mark.asyncio
async def test_fork_subagent_events_do_not_leak_to_outer_stream():
    """fork 子代理 LLM 事件不泄漏进外层流（Critical 回归）。

    真实路径：外层图 agent 节点流式调模型 → ToolNode 执行 delegate_task →
    SkillExecutor._run_fork → 真实 create_react_agent 子代理调同一 LLM。修复前
    子代理 on_chat_model_stream 经 var_child_runnable_config 传播到外层
    astream_events（metadata.langgraph_node == "agent"），token 带 _FORK_ANSWER
    内容泄漏进 SSE 并污染累积的 full_answer。
    """
    model = _ScenarioChatModel()

    graph = StateGraph(MessagesState)
    graph.add_node("agent", _make_streaming_agent_node(model))
    graph.add_node("tools", ToolNode([_make_fork_delegate_tool(model)]))
    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent",
        _route_to_tools_or_end,
        {"tools": "tools", END: END},
    )
    graph.add_edge("tools", "agent")
    compiled = graph.compile()

    init_state = cast(
        MessagesState, {"messages": [{"role": "user", "content": "用户主问题"}]}
    )
    stream_events = []
    async for event in compiled.astream_events(init_state, version="v2"):
        stream_events.append(event)

    full_answer, leaked_tokens = _collect_fork_leak_events(stream_events)
    # 1. 外层事件流不得出现携带 fork 内容的 chat_model_stream token（无泄漏）
    assert leaked_tokens == []
    # 2. 累积的 full_answer 只含主 agent 整合回答，不含子代理原文（防持久化污染）
    assert _LEAK_MARKER not in full_answer
    assert full_answer == _FINAL_ANSWER


def _make_streaming_agent_node(model: BaseChatModel):
    """构造流式 agent 节点：astream 聚合模型输出（复刻 repo agent_node 语义）。"""

    async def agent_node(state) -> dict:
        chunks = []
        async for chunk in model.astream(state["messages"]):
            chunks.append(chunk)
        result = chunks[0]
        for chunk in chunks[1:]:
            result = result + chunk
        return {"messages": [result]}

    return agent_node


def _route_to_tools_or_end(state) -> str:
    """条件路由：末条消息带工具调用 → tools；否则结束。"""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return END


@pytest.mark.asyncio
async def test_fork_with_llm_lacking_model_name_does_not_crash():
    """main_llm 无 model_name 属性：构造不抛 AttributeError，_main_model_name 回落
    None，未声明 model 的 fork 仍复用该实例正常执行（B 修复回归）。"""

    class _NoModelNameLLM:
        """无 model_name 属性的极简 llm 替身。"""

    llm = _NoModelNameLLM()
    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {"messages": [MagicMock(content="分析")]}

    with (
        patch(
            "src.agents.skills.executor.create_react_agent", return_value=fake_sub
        ) as mock_create,
        patch("src.agents.skills.executor.get_llm") as mock_get_llm,
    ):
        exe = SkillExecutor(main_llm=llm)
        rec = _record(
            context=SkillContext.FORK,
            agent_prompt="人格",
            inline_prompt=None,
            model=None,
            thinking=None,
        )
        out = await exe.execute(rec, task="分析")

    assert exe._main_model_name is None
    mock_get_llm.assert_not_called()
    args, _kwargs = mock_create.call_args
    assert args[0] is llm
    assert "分析" in out
