"""单次 fork 委派的运行态 —— 取代 ctx 上的单值字段（design D14 并发安全）。

背景：一轮内多个 delegate_task 会被 ToolNode 以 asyncio.gather 并发调度；把
delegate_id / 停止原因写在 RequestContext 的单值字段上会互相覆盖，导致 SSE 增量、
任务看板与终态判定的串号。故每次委派一个独立实例，由调用方持有并逐层传递。
"""

from dataclasses import dataclass

from src.infra.llm.request_context import RequestContext


@dataclass
class DelegateRun:
    """一次 fork 委派的全部运行态（每次调用新建，不共享）。

    Attributes:
        delegate_id: 本次委派短 id（来源：delegate_task 生成；用途：事件/看板/日志贯穿）
        skill_name: 被调用的 skill 名（来源：命中的 SkillRecord；用途：事件与日志）
        ctx: 子代理的独立请求上下文（来源：主 ctx.child()；用途：隔离引用池与计数）
        stop_reason: 中断原因（来源：executor 中断时写入；用途：终态区分 normal 与中断）
        result_text: 子代理最终文本（来源：executor 聚合；用途：直出轮写 answer）
    """

    delegate_id: str  # 本次委派短 id
    skill_name: str  # 被调用的 skill 名
    ctx: RequestContext  # 子代理独立上下文
    stop_reason: str | None = None  # None=正常完成或未执行
    result_text: str = ""  # 子代理最终文本
