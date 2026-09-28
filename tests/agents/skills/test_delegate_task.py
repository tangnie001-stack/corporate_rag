"""测试 delegate_task 工具 — inline 命中 / fork 命中 / 未知 skill / SSE delegate 事件推送。"""

import asyncio
import time
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.tools import tool as lc_tool

from src.agents.skills.delegate_run import DelegateRun
from src.agents.skills.delegate_task import DelegateTaskArgs, make_delegate_task
from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext, SkillRecord
from src.agents.skills.registry import SkillRegistry
from src.config import settings
from src.config.const import (
    DELEGATE_TASK_TITLE_TMPL,
    DelegateStopReason,
    SSEInteractionTexts,
)
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.context import RAGContext


def _record(
    name: str, context: str, body: str, model: str | None = None
) -> SkillRecord:
    if context == SkillContext.INLINE:
        return SkillRecord(
            name=name,
            description=f"{name} 规则",
            context=context,
            inline_prompt=body,
            fork_body=None,
            model=model,
            source_path=Path(f"/tmp/{name}/SKILL.md"),
        )
    return SkillRecord(
        name=name,
        description=f"{name} 专家",
        context=context,
        inline_prompt=None,
        fork_body=body,
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


@lc_tool("retrieve_kb")
def _generic_retrieve(query: str) -> str:
    """只读工具代表（通用委派工具面测试用）。"""
    return query


@lc_tool("write_doc")
def _generic_write_doc(text: str) -> str:
    """写类工具代表（通用委派工具面测试用）。"""
    return text


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
async def test_fork_auto_registers_execution_and_terminal():
    """fork 自动登记 execution（task_id=delegate_id）；正常结束置 done。"""
    import src.agents.skills.delegate_task as dt_mod
    from src.chat.task_registry import SessionTaskRegistry
    from src.config.const import TaskStatus, TaskType

    reg = SessionTaskRegistry(on_change=None)  # 不写真实 buffer，防污染
    with patch.object(dt_mod, "task_registry", reg):
        rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
        tool = make_delegate_task(
            _FakeRegistry({"finance-analyst": rec}), SkillExecutor(main_llm=MagicMock())
        )
        fake_sub = _fake_sub_agent(
            _event("on_chat_model_start"),
            _event("on_chat_model_stream", chunk=AIMessageChunk(content="分析")),
            _event("on_chat_model_end", output=AIMessage(content="分析")),
        )
        ctx = RequestContext(session_id="s1")
        token = current_request_ctx.set(ctx)
        try:
            with patch(
                "src.agents.skills.executor.create_agent", return_value=fake_sub
            ):
                await tool.ainvoke({"task": "分析", "skill": "finance-analyst"})
        finally:
            current_request_ctx.reset(token)

    running_items = [
        it for it in reg.list_session("s1") if it.status == TaskStatus.RUNNING
    ]
    assert running_items == []  # 已终态
    done = reg.list_session("s1")[0]
    assert done.type == TaskType.EXECUTION
    assert done.delegate_id and done.task_id == done.delegate_id
    assert done.status == TaskStatus.DONE and done.reason == ""


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
        with patch("src.agents.skills.executor.create_agent", return_value=fake_sub):
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
                "src.agents.skills.executor.create_agent",
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
async def test_cancel_during_fork_propagates_cancelled_with_end_reason():
    """请求取消传播（G carry）：fork 前置位 abort → 抛 CancelledError，delegate end 已推
    ok=False/reason=cancelled，finally 复位 delegate_id 与 fork_stop_reason。"""
    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    reg = _FakeRegistry({"finance-analyst": rec})
    executor = SkillExecutor(main_llm=MagicMock())
    tool = make_delegate_task(reg, executor)

    ctx = RequestContext(session_id="s1")
    ctx.abort_signal.set()  # 进入循环前置位（循环入口即抛，不依赖 FIRST_COMPLETED 竞速）
    token = current_request_ctx.set(ctx)
    try:
        fake_sub = MagicMock(
            astream_events=lambda *a, **k: _agen(
                _event("on_chat_model_stream", chunk=AIMessageChunk(content="x")),
            )
        )
        with (
            patch("src.agents.skills.executor.create_agent", return_value=fake_sub),
            pytest.raises(asyncio.CancelledError),
        ):
            await tool.ainvoke({"task": "t", "skill": "finance-analyst"})
    finally:
        current_request_ctx.reset(token)

    end = [it for it in _drain_channel(ctx) if it.get("action") == "end"][-1]
    assert end["ok"] is False and end["reason"] == "cancelled"
    # delegate_task finally 复位已执行：无残留活跃委派/停止原因
    assert ctx.delegate_id == ""
    assert ctx.fork_stop_reason is None


@pytest.mark.asyncio
async def test_cancel_mid_wait_abort_race_during_fork():
    """mid-wait abort 竞速（E carry）：fork 事件源首事件后挂起，兄弟 task 延迟置位
    abort_signal → executor 阻塞在 FIRST_COMPLETED wait 收到 abort（executor abort-in-done
    分支，executor.py:229-236）→ CancelledError，end reason=cancelled，finally 复位。"""
    import src.agents.skills.executor as exec_mod

    first_chunk_seen = asyncio.Event()

    async def _slow(*a, **k):
        yield _event("on_chat_model_stream", chunk=AIMessageChunk(content="a"))
        # 仅在 executor 请求第 2 个事件（__anext__ #2）时执行：此刻 executor 必已进入 wait
        first_chunk_seen.set()
        await asyncio.sleep(30)  # 挂起等待：__anext__ 长期 pending，等 abort 中断
        yield _event(
            "on_chat_model_stream", chunk=AIMessageChunk(content="b")
        )  # pragma: no cover

    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    reg = _FakeRegistry({"finance-analyst": rec})
    executor = SkillExecutor(main_llm=MagicMock())
    tool = make_delegate_task(reg, executor)

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)

    async def _abort_later():
        await first_chunk_seen.wait()
        await asyncio.sleep(0.01)  # 留出事件循环切换，保证 executor 已阻塞在 wait 内
        ctx.abort_signal.set()

    setter = asyncio.create_task(_abort_later())
    try:
        with (
            patch(
                "src.agents.skills.executor.create_agent",
                return_value=MagicMock(astream_events=_slow),
            ),
            # 放大空闲阈值，防止 idle 在 abort 前抢先中断（本用例只测 abort 竞速路径）
            patch.object(exec_mod.settings, "DELEGATE_MAX_IDLE_S", 5),
            pytest.raises(asyncio.CancelledError),
        ):
            await tool.ainvoke({"task": "分析年报", "skill": "finance-analyst"})
    finally:
        setter.cancel()
        await asyncio.gather(setter, return_exceptions=True)
        current_request_ctx.reset(token)

    assert ctx.abort_signal.is_set()  # abort 确在运行期间置位（真实竞速，非前置位）
    end = [it for it in _drain_channel(ctx) if it.get("action") == "end"][-1]
    assert end["ok"] is False and end["reason"] == "cancelled"
    assert ctx.delegate_id == ""
    assert ctx.fork_stop_reason is None


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
    tools = make_rag_tools(MagicMock(), MagicMock(), MagicMock(), delegate_task=tool)
    names = [t.name for t in tools]
    assert "delegate_task" in names


def test_make_rag_tools_without_delegate_keeps_fixed_set():
    """未传 delegate_task → 工具列表保持既有集合（无 delegate_task）。"""
    from src.agents.tools.rag_tools import make_rag_tools

    tools = make_rag_tools(MagicMock(), MagicMock(), MagicMock())
    names = [t.name for t in tools]
    assert "delegate_task" not in names


class _StubExecutor(SkillExecutor):
    """替身执行器：记录收到的 run，并模拟子代理向自己的上下文写检索结果。"""

    def __init__(self, stop_reason: str | None = None, output: str = "子代理结论"):
        super().__init__(main_llm=MagicMock())
        self.seen_run: DelegateRun | None = None
        self.stop_reason = stop_reason
        self.output = output

    async def execute(self, record, task, run=None):
        self.seen_run = run
        if run is not None:
            run.ctx.tool_contexts.append(cast(RAGContext, _Ctx("子代理材料")))
            run.stop_reason = self.stop_reason
        return self.output


class _Ctx:
    def __init__(self, content: str):
        self.content = content


@pytest.mark.asyncio
async def test_delegate_passes_run_and_isolates_pool():
    """委派路径必须自建 DelegateRun：子代理写入落子池，主池保持为空。"""
    parent = RequestContext(session_id="s1", kb_id="k1", kb_bound=True)
    token = current_request_ctx.set(parent)
    executor = _StubExecutor()
    tool = make_delegate_task(
        _FakeRegistry({"analyst": _record("analyst", SkillContext.FORK, "正文")}),
        executor,
    )
    try:
        out = await tool.ainvoke({"task": "任务", "skill": "analyst"})
    finally:
        current_request_ctx.reset(token)
    assert out == "子代理结论"
    assert executor.seen_run is not None
    assert executor.seen_run.ctx is not parent  # 独立子上下文
    assert executor.seen_run.ctx.tool_contexts  # 写入落在子池
    assert parent.tool_contexts == []  # 主池未被污染


@pytest.mark.asyncio
async def test_delegate_end_reason_from_run_not_main_ctx():
    """中断原因必须从 run 读：executor 写在 run 上，读主 ctx 会恒 None 而误记 normal。"""
    parent = RequestContext(session_id="s1", kb_id="k1", kb_bound=True)
    token = current_request_ctx.set(parent)
    executor = _StubExecutor(stop_reason=DelegateStopReason.TURN)
    tool = make_delegate_task(
        _FakeRegistry({"analyst": _record("analyst", SkillContext.FORK, "正文")}),
        executor,
    )
    try:
        await tool.ainvoke({"task": "任务", "skill": "analyst"})
    finally:
        current_request_ctx.reset(token)
    events = []
    while not parent.clarify_channel.empty():
        events.append(parent.clarify_channel.get_nowait())
    end = [e for e in events if e.get("action") == "end"]
    assert len(end) == 1
    assert end[0]["ok"] is False
    assert end[0]["reason"] == DelegateStopReason.TURN.value


@pytest.mark.asyncio
async def test_delegate_strips_confirm_marker_prefix_keeps_question():
    """委派路径剥掉 `CONFIRM_REQUIRED:` 协议前缀，但保留问题文本（主 agent 据此决定是否提问）。"""
    parent = RequestContext(session_id="s1", kb_id="k1", kb_bound=True)
    token = current_request_ctx.set(parent)
    executor = _StubExecutor(
        output="先给结论。\nCONFIRM_REQUIRED: 请提供公司代码\n其余内容照旧。"
    )
    tool = make_delegate_task(
        _FakeRegistry({"analyst": _record("analyst", SkillContext.FORK, "正文")}),
        executor,
    )
    try:
        out = await tool.ainvoke({"task": "任务", "skill": "analyst"})
    finally:
        current_request_ctx.reset(token)
    assert "CONFIRM_REQUIRED" not in out
    assert "请提供公司代码" in out


@pytest.mark.asyncio
async def test_delegate_fail_open_without_ctx_strips_confirm_marker():
    """无请求上下文（fail-open 仍走主上下文）时同样剥确认标记前缀、只留问题文本。

    覆盖 delegate_task 的 `ctx is None` 早退分支：不隔离（无父上下文可派生，仍用
    主上下文执行），但交回主 agent 的文本仍不得外泄 `CONFIRM_REQUIRED:` 协议串。
    """
    executor = _StubExecutor(
        output="先给结论。\nCONFIRM_REQUIRED: 请提供公司代码\n其余内容照旧。"
    )
    tool = make_delegate_task(
        _FakeRegistry({"analyst": _record("analyst", SkillContext.FORK, "正文")}),
        executor,
    )
    # 不设置 current_request_ctx —— 命中 ctx is None 的 fail-open 早退分支
    assert current_request_ctx.get() is None
    out = await tool.ainvoke({"task": "任务", "skill": "analyst"})
    assert "CONFIRM_REQUIRED" not in out
    assert "请提供公司代码" in out


@pytest.mark.asyncio
async def test_budget_exhausted_returns_readable_reason(monkeypatch):
    """触顶：返回可读原因、不抛异常、**不启动子代理**（且提示不要再重试）。"""
    from src.chat.delegate_budget import delegate_budget

    monkeypatch.setattr(
        delegate_budget, "_counts", {"s1": settings.DELEGATE_MAX_PER_SESSION}
    )
    # _touched 置为当前时间：避免 check_and_incr 的 TTL 惰性清理把已触顶的计数整条删除
    monkeypatch.setattr(delegate_budget, "_touched", {"s1": time.time()})
    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    tool = make_delegate_task(
        _FakeRegistry({"finance-analyst": rec}), SkillExecutor(main_llm=MagicMock())
    )
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch(
            "src.agents.skills.executor.create_agent",
            side_effect=AssertionError("触顶时不得启动子代理"),
        ):
            out = await tool.ainvoke({"task": "分析", "skill": "finance-analyst"})
    finally:
        current_request_ctx.reset(token)
    assert "上限" in out
    assert "不要" in out


@pytest.mark.asyncio
async def test_inline_hit_does_not_consume_budget(monkeypatch):
    """inline 命中不启动子代理 → 不消耗预算。"""
    from src.chat.delegate_budget import delegate_budget

    monkeypatch.setattr(delegate_budget, "_counts", {})
    monkeypatch.setattr(delegate_budget, "_touched", {})
    rec = _record("finance-qa", SkillContext.INLINE, "请按规则作答：{task}")
    tool = make_delegate_task(
        _FakeRegistry({"finance-qa": rec}), SkillExecutor(main_llm=MagicMock())
    )
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        await tool.ainvoke({"task": "2024营收多少", "skill": "finance-qa"})
    finally:
        current_request_ctx.reset(token)
    assert delegate_budget.used("s1") == 0


@pytest.mark.asyncio
async def test_successful_fork_consumes_one_budget():
    """一次正常完成的 fork 委派消耗一次预算：used 从 0 变 1。

    守住调用点走的是 check_and_incr（自增），而非只读检查（`used(...) >= limit`）——
    后者计数永不增长、闸门永不触发，而"触顶被拒"用例因预置计数仍会全绿。
    """
    import src.agents.skills.delegate_task as dt_mod
    from src.chat.delegate_budget import delegate_budget
    from src.chat.task_registry import SessionTaskRegistry

    sid = "s-budget-consume"
    delegate_budget.reset(sid)  # 共享单例为模块级，先清该会话避免残留
    reg = SessionTaskRegistry(on_change=None)  # 不写真实 buffer，防污染
    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    tool = make_delegate_task(
        _FakeRegistry({"finance-analyst": rec}), SkillExecutor(main_llm=MagicMock())
    )
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="分析")),
        _event("on_chat_model_end", output=AIMessage(content="分析")),
    )
    ctx = RequestContext(session_id=sid)
    token = current_request_ctx.set(ctx)
    try:
        assert delegate_budget.used(sid) == 0
        with (
            patch.object(dt_mod, "task_registry", reg),
            patch("src.agents.skills.executor.create_agent", return_value=fake_sub),
        ):
            await tool.ainvoke({"task": "分析", "skill": "finance-analyst"})
        assert delegate_budget.used(sid) == 1
    finally:
        current_request_ctx.reset(token)
        delegate_budget.reset(sid)  # 清该会话，避免影响同文件其它用例


@pytest.mark.asyncio
async def test_budget_skip_logged(monkeypatch):
    """触顶记 delegate skip / reason=budget_exhausted。"""
    import src.agents.skills.delegate_task as dt_mod
    from src.chat.delegate_budget import delegate_budget

    monkeypatch.setattr(
        delegate_budget, "_counts", {"s1": settings.DELEGATE_MAX_PER_SESSION}
    )
    # _touched 置为当前时间：避免 TTL 惰性清理清空已触顶计数（同触顶用例）
    monkeypatch.setattr(delegate_budget, "_touched", {"s1": time.time()})
    captured: list[tuple] = []
    monkeypatch.setattr(
        dt_mod.core_logging,
        "log_event",
        lambda ev, **kw: captured.append((ev.value, kw)),
    )
    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    tool = make_delegate_task(
        _FakeRegistry({"finance-analyst": rec}), SkillExecutor(main_llm=MagicMock())
    )
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        await tool.ainvoke({"task": "分析", "skill": "finance-analyst"})
    finally:
        current_request_ctx.reset(token)
    assert ("delegate skip", {"reason": "budget_exhausted"}) in captured


@pytest.mark.asyncio
async def test_generic_delegation_without_skill(monkeypatch):
    """省略 skill → 通用委派：不查注册表、task 直接作子代理输入、事件与看板仍有条目。"""
    import src.agents.skills.delegate_task as dt_mod
    from src.chat.task_registry import SessionTaskRegistry

    board = SessionTaskRegistry(on_change=None)  # 不写真实 buffer，防污染
    tool = make_delegate_task(
        _FakeRegistry({}),
        SkillExecutor(main_llm=MagicMock()),  # 空注册表：若去查就会走未知 skill
    )
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="结论")),
        _event("on_chat_model_end", output=AIMessage(content="结论")),
    )
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with (
            patch.object(dt_mod, "task_registry", board),
            patch("src.agents.skills.executor.create_agent", return_value=fake_sub),
        ):
            out = await tool.ainvoke({"task": "帮我查一下某公司近三年的营收"})
            items = board.list_session("s1")
    finally:
        current_request_ctx.reset(token)
    assert "结论" in out
    assert items[0].title == DELEGATE_TASK_TITLE_TMPL.format(
        skill=SSEInteractionTexts.DELEGATE_GENERIC_LABEL
    )
    # 事件 skill 字段钉住用户可见标签（前端据此渲染分节标题；不得回退为日志占位）
    delegate_items = [it for it in _drain_channel(ctx) if it.get("type") == "delegate"]
    assert delegate_items[0]["action"] == "start"
    assert delegate_items[-1]["action"] == "end"
    assert delegate_items[0]["skill"] == SSEInteractionTexts.DELEGATE_GENERIC_LABEL
    assert delegate_items[-1]["skill"] == SSEInteractionTexts.DELEGATE_GENERIC_LABEL


@pytest.mark.asyncio
async def test_generic_delegation_inherits_readonly_tools(monkeypatch):
    """通用委派同样继承只读工具面（含 retrieve_kb），且不含写类/禁用集。"""
    from src.agents.tools import readonly as readonly_module

    monkeypatch.setattr(
        readonly_module, "_TOOL_READONLY", {"retrieve_kb": True, "write_doc": False}
    )

    captured: dict = {}

    def _fake_create_agent(*args, **kwargs):
        captured.update(kwargs)
        return _fake_sub_agent(
            _event("on_chat_model_end", output=AIMessage(content="ok"))
        )

    executor = SkillExecutor(
        main_llm=MagicMock(),
        tool_provider=lambda: [_generic_retrieve, _generic_write_doc],
    )
    tool = make_delegate_task(_FakeRegistry({}), executor)
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch(
            "src.agents.skills.executor.create_agent", side_effect=_fake_create_agent
        ):
            await tool.ainvoke({"task": "查营收"})
    finally:
        current_request_ctx.reset(token)
    assert [t.name for t in captured["tools"]] == ["retrieve_kb"]


@pytest.mark.asyncio
async def test_generic_delegation_consumes_budget():
    """通用委派（省略 skill）同样过预算闸门：一次成功委派 used 从 0 变 1。

    守住"两条分支共用同一闸门"——通用分支不得写成绕过闸门的独立早退路径。
    """
    import src.agents.skills.delegate_task as dt_mod
    from src.chat.delegate_budget import delegate_budget
    from src.chat.task_registry import SessionTaskRegistry

    sid = "s-generic-budget"
    delegate_budget.reset(sid)  # 共享单例为模块级，先清该会话避免残留
    reg = SessionTaskRegistry(on_change=None)
    tool = make_delegate_task(_FakeRegistry({}), SkillExecutor(main_llm=MagicMock()))
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_end", output=AIMessage(content="结论")),
    )
    ctx = RequestContext(session_id=sid)
    token = current_request_ctx.set(ctx)
    try:
        assert delegate_budget.used(sid) == 0
        with (
            patch.object(dt_mod, "task_registry", reg),
            patch("src.agents.skills.executor.create_agent", return_value=fake_sub),
        ):
            await tool.ainvoke({"task": "帮我查营收"})
        assert delegate_budget.used(sid) == 1
    finally:
        current_request_ctx.reset(token)
        delegate_budget.reset(sid)  # 清该会话，避免影响同文件其它用例


@pytest.mark.asyncio
async def test_generic_delegation_rejected_when_budget_exhausted(monkeypatch):
    """通用委派触顶同样被拒：返回可读原因且不启动子代理（共用同一闸门）。"""
    from src.chat.delegate_budget import delegate_budget

    monkeypatch.setattr(
        delegate_budget, "_counts", {"s1": settings.DELEGATE_MAX_PER_SESSION}
    )
    # _touched 置为当前时间：避免 check_and_incr 的 TTL 惰性清理把已触顶的计数整条删除
    monkeypatch.setattr(delegate_budget, "_touched", {"s1": time.time()})
    tool = make_delegate_task(_FakeRegistry({}), SkillExecutor(main_llm=MagicMock()))
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch(
            "src.agents.skills.executor.create_agent",
            side_effect=AssertionError("触顶时不得启动子代理"),
        ):
            out = await tool.ainvoke({"task": "帮我查营收"})
    finally:
        current_request_ctx.reset(token)
    assert "上限" in out
    assert "不要" in out
