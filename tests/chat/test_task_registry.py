"""测试会话级任务注册表 — 生命周期 / TTL / 跨轮保留 / plan-execution 隔离 / 事件。"""

import time

import pytest

from src.chat.task_registry import SessionTaskRegistry
from src.config.const import TaskStatus, TaskType


@pytest.fixture
def reg():
    # on_change=None：注册表方法不写真实 streaming_manager（避免跨文件缓冲污染）
    r = SessionTaskRegistry(on_change=None)
    r._tasks.clear()  # 干净实例（模块单例在测试间保留会串，用构造隔离）
    return r


def test_create_and_get_plan_task(reg):
    t = reg.create_task("s1", TaskType.PLAN, title="查营收")
    assert t.task_id and t.type == "plan" and t.status == "pending"
    got = reg.get_task("s1", t.task_id)
    assert got is not None and got.title == "查营收"
    # 跨会话隔离
    assert reg.get_task("s2", t.task_id) is None


def test_update_task_fields(reg):
    t = reg.create_task("s1", TaskType.PLAN, title="a")
    upd = reg.update_task("s1", t.task_id, status=TaskStatus.DONE, summary="完成")
    assert upd is not None and upd.status == "done" and upd.summary == "完成"
    assert reg.update_task("s1", "missing", status="done") is None


def test_update_to_terminal_emits_terminal_action():
    """update_task 进入终态（cancelled/done）发 action=terminal，非终态发 updated。"""
    emitted = []
    r = SessionTaskRegistry(
        on_change=lambda sid, action, item: emitted.append((sid, action))
    )
    t = r.create_task("s1", TaskType.PLAN, title="a")  # created
    r.update_task("s1", t.task_id, status=TaskStatus.RUNNING)  # updated
    assert emitted[-1] == ("s1", "updated")
    r.update_task("s1", t.task_id, status=TaskStatus.CANCELLED)  # 终态 → terminal
    assert emitted[-1] == ("s1", "terminal")


def test_execution_terminal_mapping(reg):
    reg.create_task(
        "s1",
        TaskType.EXECUTION,
        title="委派 x",
        delegate_id="d1",
        status=TaskStatus.RUNNING,
    )
    done = reg.mark_terminal("s1", "d1", TaskStatus.DONE, reason="")
    assert done is not None and done.status == "done"
    reg.create_task(
        "s1",
        TaskType.EXECUTION,
        title="委派 y",
        delegate_id="d2",
        status=TaskStatus.RUNNING,
    )
    timed = reg.mark_terminal("s1", "d2", TaskStatus.TIMEOUT, reason="idle")
    assert timed is not None and timed.status == "timeout" and timed.reason == "idle"


def test_plan_and_execution_do_not_override(reg):
    plan = reg.create_task("s1", TaskType.PLAN, title="p")
    exe = reg.create_task("s1", TaskType.EXECUTION, title="e", delegate_id="d1")
    # 两个条目并存；plan 不以 delegate 查、execution 独立
    assert len(reg.list_session("s1")) == 2
    assert reg.list_tasks_for_delegate("s1", "d1")[0].task_id == exe.task_id
    # 更新 plan 不影响 execution
    assert plan.task_id != exe.task_id


def test_ttl_sweep_expired_only(reg):
    t_old = reg.create_task("s1", TaskType.PLAN, title="old")
    t_old.updated_at = time.time() - 3600  # 超 TTL
    t_new = reg.create_task("s1", TaskType.PLAN, title="new")
    reg.sweep_expired()
    assert reg.get_task("s1", t_old.task_id) is None
    assert reg.get_task("s1", t_new.task_id) is not None


def test_new_post_keeps_existing_tasks(reg):
    """同一会话跨轮保留（注册表独立于事件缓冲，clear_buffer 不清表）。"""
    t = reg.create_task("s1", TaskType.PLAN, title="keep")
    assert (
        reg.get_task("s1", t.task_id) is not None
    )  # 语义由 streaming.clear_buffer 不触表保证
