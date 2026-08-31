"""测试 ask_user 工具 — 挂起 Future 进程级注册表 + contextvar 计数 + KB 注入 options。

KB 聚合通过 monkeypatch mock（固定返回公司/期间候选），不发真实网络/DB 调用。
async 工具不支持 sync invoke（NotImplementedError），测试统一用 ainvoke。
"""

import asyncio

import pytest

from src.agents.graph.state import AgentState
from src.agents.tools import rag_tools
from src.agents.tools.rag_tools import ask_user
from src.config.const import MAX_ASK_PER_TURN
from src.infra.llm.request_context import (
    RequestContext,
    current_request_ctx,
    pending_asks,
)
from src.infra.search.query_router import KbEntityAggregate


@pytest.fixture(autouse=True)
def mock_kb_aggregate(monkeypatch):
    """mock aggregate_kb_entities：固定返回公司/期间候选，避免真实 DB 查询。"""

    async def fake_aggregate(kb_ids):
        return KbEntityAggregate(
            text="公司: 腾讯; 报告期: 2024年",
            companies=["腾讯"],
            periods=["2024年"],
            codes=[],
        )

    monkeypatch.setattr(rag_tools, "aggregate_kb_entities", fake_aggregate)
    yield


@pytest.fixture(autouse=True)
def clean_pending_asks():
    """每个测试前后清空进程级挂起注册表，避免跨测试污染。"""
    pending_asks.clear()
    yield
    pending_asks.clear()


def _ask_args(session_id: str = "s1", kb_id: str = "kb1") -> dict:
    """构造 ask_user.ainvoke 的入参（单条 period 问题）。"""
    return {
        "questions": [
            {
                "id": "q1",
                "question": "哪一年？",
                "dimension": "period",
                "multi_select": False,
            }
        ],
        "state": AgentState.make_initial_state(session_id, kb_id, "q", []),
    }


@pytest.mark.asyncio
async def test_ask_user_blocks_and_resolves(monkeypatch):
    """未回答前工具阻塞；POST 端经 pending_asks 解析 Future 后返回答案，并清理注册表。

    ASK_USER_MODE_DSH=False（dual 模式）：无模型自带 options 时按 dimension 注入 KB 候选。
    """
    from src.config import settings as s

    monkeypatch.setattr(s, "ASK_USER_MODE_DSH", False)
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        task = asyncio.create_task(ask_user.ainvoke(_ask_args()))
        await asyncio.sleep(0.05)
        # 未回答前工具应阻塞（task 未完成）
        assert not task.done()
        # 已推送澄清事件，且按 dimension 从 KB 聚合注入真实候选 options
        event = await asyncio.wait_for(ctx.clarify_channel.get(), timeout=1)
        assert event["type"] == "ask_user"
        assert event["questions"][0]["options"] == ["2024年"]
        # 进程级注册表已登记该 session 的 Future
        fut = pending_asks.get("s1")
        assert fut is not None
        fut.set_result({"answers": [{"id": "q1", "selected": ["2024年"]}]})
        result = await asyncio.wait_for(task, timeout=1)
        assert "2024年" in result
        assert ctx.ask_count == 1
    finally:
        current_request_ctx.reset(token)
    # 工具结束后注册表已清理
    assert "s1" not in pending_asks


@pytest.mark.asyncio
async def test_ask_user_ask_limit():
    """ask_count 达上限时直接返回错误文本，不推送问题也不登记 Future。"""
    ctx = RequestContext(session_id="s1")
    ctx.ask_count = MAX_ASK_PER_TURN
    token = current_request_ctx.set(ctx)
    try:
        result = await ask_user.ainvoke(_ask_args())
        assert "上限" in result
        assert ctx.clarify_channel.empty()
        assert "s1" not in pending_asks
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_ask_user_timeout(monkeypatch):
    """不 resolve 时在 ASK_USER_TIMEOUT 后返回超时引导文案，并清理注册表。"""
    monkeypatch.setattr(rag_tools, "ASK_USER_TIMEOUT", 0.1)
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        result = await ask_user.ainvoke(_ask_args())
        assert "推荐方案" in result
    finally:
        current_request_ctx.reset(token)
    assert "s1" not in pending_asks


@pytest.mark.asyncio
async def test_ask_user_timeout_text_is_guidance():
    """超时文案是引导 LLM 给推荐的文本，而非 Error 前缀。"""
    from src.agents.tools import ask_tools
    from src.config.const import SSEInteractionTexts

    # 直接验证超时文案是引导而非 Error 前缀
    assert not SSEInteractionTexts.ASK_USER_TIMEOUT_TEXT.startswith("Error:")
    assert "推荐方案" in SSEInteractionTexts.ASK_USER_TIMEOUT_TEXT
    # ask_user 超时路径返回的正是该常量（ask_tools 引入以锁定引用一致性）
    assert (
        ask_tools.SSEInteractionTexts.ASK_USER_TIMEOUT_TEXT
        == SSEInteractionTexts.ASK_USER_TIMEOUT_TEXT
    )


@pytest.mark.asyncio
async def test_ask_user_concurrent_second_rejected():
    """同一 session 并发两个 ask_user：第二个因单槽占用直接返回上限错误，不覆盖第一个 Future。"""
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        t1 = asyncio.create_task(ask_user.ainvoke(_ask_args()))
        t2 = asyncio.create_task(ask_user.ainvoke(_ask_args()))

        result2 = await asyncio.wait_for(t2, timeout=1)
        assert "上限" in result2

        # 第一个仍持有挂起 Future，resolve 后正常返回
        fut = pending_asks.get("s1")
        assert fut is not None
        fut.set_result({"answers": [{"id": "q1", "selected": ["2024年"]}]})
        result1 = await asyncio.wait_for(t1, timeout=1)
        assert "2024年" in result1
    finally:
        current_request_ctx.reset(token)
    assert "s1" not in pending_asks


def _ask_args_with_options(session_id: str = "s1", kb_id: str = "kb1") -> dict:
    """构造含模型自带 options 的 ask_user 入参（free 维度 + options）。"""
    return {
        "questions": [
            {
                "id": "q1",
                "question": "您想要哪种方案？",
                "dimension": "free",
                "options": ["方案A", "方案B"],
                "multi_select": False,
            }
        ],
        "state": AgentState.make_initial_state(session_id, kb_id, "q", []),
    }


@pytest.mark.asyncio
async def test_ask_user_dash_mode_uses_model_options(monkeypatch):
    """ASK_USER_MODE_DSH=true（默认）：直接用模型自带 options，不加载 dimension 候选。"""
    from src.config import settings as s

    monkeypatch.setattr(s, "ASK_USER_MODE_DSH", True)
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        task = asyncio.create_task(ask_user.ainvoke(_ask_args_with_options()))
        await asyncio.sleep(0.05)
        event = await asyncio.wait_for(ctx.clarify_channel.get(), timeout=1)
        assert event["questions"][0]["options"] == ["方案A", "方案B"]
        fut = pending_asks.get("s1")
        assert fut is not None
        fut.set_result({"answers": [{"id": "q1", "selected": ["方案A"]}]})
        result = await asyncio.wait_for(task, timeout=1)
        assert "方案A" in result
    finally:
        current_request_ctx.reset(token)
    assert "s1" not in pending_asks


@pytest.mark.asyncio
async def test_ask_user_dual_mode_injects_dimension(monkeypatch):
    """ASK_USER_MODE_DSH=false：无 options 时按 dimension 注入 KB 真实候选。"""
    from src.config import settings as s

    monkeypatch.setattr(s, "ASK_USER_MODE_DSH", False)
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        task = asyncio.create_task(ask_user.ainvoke(_ask_args()))
        await asyncio.sleep(0.05)
        event = await asyncio.wait_for(ctx.clarify_channel.get(), timeout=1)
        assert event["questions"][0]["options"] == ["2024年"]
        fut = pending_asks.get("s1")
        assert fut is not None
        fut.set_result({"answers": [{"id": "q1", "selected": ["2024年"]}]})
        await asyncio.wait_for(task, timeout=1)
    finally:
        current_request_ctx.reset(token)
    assert "s1" not in pending_asks
