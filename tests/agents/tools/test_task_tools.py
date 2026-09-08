"""测试 Task 工具集 — 会话级读写 / plan 条目 / stop 语义。"""

from unittest.mock import patch

import pytest

from src.agents.tools.task_tools import make_task_tools
from src.config.const import TaskStatus, TaskType
from src.infra.llm.request_context import RequestContext, current_request_ctx


def _tools():
    return {t.name: t for t in make_task_tools()}


@pytest.mark.asyncio
async def test_task_create_and_list_roundtrip():
    """create → 返回 task_id；list 可读（ctx.session_id 定位）。"""
    import src.agents.tools.task_tools as tt
    from src.chat.task_registry import SessionTaskRegistry

    reg = SessionTaskRegistry(on_change=None)  # 不写真实 buffer，防污染
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch.object(tt, "task_registry", reg):
            tools = _tools()
            out = await tools["task_create"].ainvoke({"title": "梳理报告口径"})
            assert "task_id" in out or "任务已创建" in out
            listing = await tools["task_list"].ainvoke({})
            assert "梳理报告口径" in listing
            items = reg.list_session("s1")
            assert items and items[0].type == TaskType.PLAN
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_task_stop_cancels_without_aborting_request():
    """stop 语义（已收敛，openspec design D3/tasks 2.1 同步标注）：
    置 cancelled 且不 set abort_signal——fork 阻塞式执行内主 agent 无法并行调 task_stop，
    跨请求 delegate 属旧请求 ContextVar 触达不到，运行中 delegate 的真实取消走 cancel 端点
    （core：fork 响应同一 ctx.abort_signal 收敛为 reason=cancelled）。
    """
    import src.agents.tools.task_tools as tt
    from src.chat.task_registry import SessionTaskRegistry
    from src.config.const import TaskType

    reg = SessionTaskRegistry(on_change=None)  # 不写真实 buffer，防污染
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch.object(tt, "task_registry", reg):
            reg.create_task(
                "s1",
                TaskType.EXECUTION,
                title="e",
                delegate_id="d1",
                status=TaskStatus.RUNNING,
            )
            tools = _tools()
            out = await tools["task_stop"].ainvoke({"task_id": "d1"})
            item = reg.get_task("s1", "d1")
            assert item is not None and item.status == TaskStatus.CANCELLED
            assert "取消" in out
            assert ctx.abort_signal.is_set() is False  # 不误杀主请求
    finally:
        current_request_ctx.reset(token)
