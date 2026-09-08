"""Task 工具集 — 会话级任务注册表的读写（仅主 agent 工具面，前端只读）。

工具：create/get/list/update/output/stop 最小集。经 ctx.session_id 定位会话；
无请求上下文（ctx None）时返回错误文案给 LLM。

**stop 语义（已收敛，见 openspec design D3/tasks 2.1 标注）**：fork 在 ToolNode 内
阻塞式串行执行——主 agent 无法在 delegate 运行期间并行调 task_stop；跨请求 delegate
属旧请求 ContextVar，本请求 abort 触达不到；同请求竞态 set abort 又等于取消整个主请求
（非仅该 delegate）。因此 task_stop **不**置位 ctx.abort_signal，仅对非终态条目
（含残留 running/pending）置 cancelled；运行中 delegate 的真实取消收敛到 cancel 端点
（core：fork 响应同一 ctx.abort_signal，中断原因=cancelled，delegate end/注册表终态一致）。
"""

import json

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.chat.task_registry import task_registry
from src.config.const import TaskStatus, TaskType
from src.infra.llm.request_context import current_request_ctx


class TaskCreateArgs(BaseModel):
    """task_create 入参（LLM 可见）。"""

    title: str = Field(description="任务标题（要跟踪的事项）")
    status: str = Field(
        default=TaskStatus.PENDING, description="初始状态（pending/running/done）"
    )
    stage: str = Field(default="", description="当前阶段（可选）")


class TaskUpdateArgs(BaseModel):
    """task_update 入参。"""

    task_id: str = Field(description="要更新的任务 id（task_create/task_list 获得）")
    status: str | None = Field(
        default=None, description="新状态（pending/running/done/failed）"
    )
    stage: str | None = Field(default=None, description="新阶段")
    summary: str | None = Field(default=None, description="进展摘要")
    dependencies: list[str] | None = Field(
        default=None, description="依赖任务 id 列表（可选，覆盖声明）"
    )


class TaskStopArgs(BaseModel):
    """task_stop 入参。"""

    task_id: str = Field(description="要停止的任务 id")


def _ctx_session_id() -> str:
    """取当前请求会话 id；无上下文返回空串（工具将报错）。"""
    ctx = current_request_ctx.get()
    if ctx is None:
        return ""
    return ctx.session_id


def make_task_tools() -> list:
    """构建 Task 工具列表（create/get/list/update/output/stop）。"""

    @tool("task_create", args_schema=TaskCreateArgs)
    async def task_create(
        title: str, status: str = TaskStatus.PENDING, stage: str = ""
    ) -> str:
        """创建一条计划任务用于跟踪进展，返回 task_id。"""
        session_id = _ctx_session_id()
        if not session_id:
            return "Error: 请求上下文不可用"
        item = task_registry.create_task(
            session_id, TaskType.PLAN, title=title, status=status, stage=stage
        )
        return f"任务已创建，task_id={item.task_id}"

    @tool("task_get", args_schema=TaskStopArgs)
    async def task_get(task_id: str) -> str:
        """按 task_id 查任务详情。"""
        session_id = _ctx_session_id()
        if not session_id:
            return "Error: 请求上下文不可用"
        item = task_registry.get_task(session_id, task_id)
        if item is None:
            return f"任务不存在: {task_id}"
        return json.dumps(item.to_dict(), ensure_ascii=False)

    @tool("task_list")
    async def task_list() -> str:
        """列出当前会话全部任务（含 id/标题/状态），用于跟踪或引用 task_id。"""
        session_id = _ctx_session_id()
        if not session_id:
            return "Error: 请求上下文不可用"
        items = task_registry.list_session(session_id)
        if not items:
            return "当前会话暂无任务"
        lines = [f"- {it.task_id}: {it.title} [{it.status}]" for it in items]
        return "\n".join(lines)

    @tool("task_update", args_schema=TaskUpdateArgs)
    async def task_update(
        task_id: str,
        status: str | None = None,
        stage: str | None = None,
        summary: str | None = None,
        dependencies: list[str] | None = None,
    ) -> str:
        """更新任务状态/阶段/摘要/依赖。"""
        session_id = _ctx_session_id()
        if not session_id:
            return "Error: 请求上下文不可用"
        item = task_registry.update_task(
            session_id,
            task_id,
            status=status,
            stage=stage,
            summary=summary,
            dependencies=dependencies,
        )
        if item is None:
            return f"任务不存在: {task_id}"
        return f"任务已更新: {task_id} [{item.status}]"

    @tool("task_output", args_schema=TaskStopArgs)
    async def task_output(task_id: str) -> str:
        """返回任务当前输出（阶段与摘要），供继续作答参考。"""
        session_id = _ctx_session_id()
        if not session_id:
            return "Error: 请求上下文不可用"
        item = task_registry.get_task(session_id, task_id)
        if item is None:
            return f"任务不存在: {task_id}"
        return json.dumps(
            {
                "task_id": item.task_id,
                "status": item.status,
                "stage": item.stage,
                "summary": item.summary,
            },
            ensure_ascii=False,
        )

    @tool("task_stop", args_schema=TaskStopArgs)
    async def task_stop(task_id: str) -> str:
        """停止指定任务：置为 cancelled。

        收敛语义：不置位 ctx.abort_signal（阻塞式 fork 下跨请求 delegate 触达不到，
        同请求竞态会误杀整个主请求）；运行中 delegate 的真实取消由用户走 cancel 端点
        （core：fork 响应 ctx.abort_signal → reason=cancelled → 注册表终态）。
        本工具用于主 agent 主动清理/收尾非终态条目（含残留 running/pending）。
        """
        ctx = current_request_ctx.get()
        if ctx is None:
            return "Error: 请求上下文不可用"
        item = task_registry.get_task(ctx.session_id, task_id)
        if item is None:
            return f"任务不存在: {task_id}"
        if item.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
            task_registry.update_task(
                ctx.session_id, task_id, status=TaskStatus.CANCELLED
            )
            return f"任务已取消: {task_id}"
        return f"任务已是终态（{item.status}），无需停止"

    return [task_create, task_get, task_list, task_update, task_output, task_stop]
