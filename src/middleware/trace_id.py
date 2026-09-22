"""TraceID 中间件 — 在请求入口生成/提取 trace_id，注入全链路。

优先级: X-Trace-ID 请求头 → ?trace_id 查询参数 → uuid4 自动生成。
响应头 X-Trace-ID 统一回传，覆盖正常/异常/SSE 全部场景。
"""

from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from src.infra.llm.trace_context import current_trace_id
from src.infra.llm.tracing import is_valid_trace_id, new_trace_id


async def trace_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # 1. 获取 trace_id：header → query → auto-generate
    #    入站值是不可信输入：接线后它会成为 Langfuse 的 trace 主键（upsert），
    #    非法值必须在此拦下并换成服务端生成值 —— 若留到下游再换，响应头（用的是
    #    下面的局部变量）会与 SSE done / Langfuse（都重读 contextvar）分叉。
    inbound = request.headers.get("X-Trace-ID")
    if not inbound:
        inbound = request.query_params.get("trace_id")
    if inbound and is_valid_trace_id(inbound):
        trace_id = inbound
    else:
        trace_id = new_trace_id()

    # 2. 注入 request.state 和 contextvar
    request.state.trace_id = trace_id
    current_trace_id.set(trace_id)

    # 3. 继续处理请求
    response: Response = await call_next(request)

    # 4. 回写响应头（覆盖所有返回路径）
    response.headers["X-Trace-ID"] = trace_id
    return response
