"""SSE (Server-Sent Events) 格式化工具函数。

提供统一的 SSE 事件文本构建函数，供流式聊天端点使用。
所有函数仅依赖标准库 json，无业务依赖。
"""

import json
from dataclasses import dataclass, field

# ── 结构化事件 dataclass ─────────────────────────────────


@dataclass
class SSEStatusEvent:
    """图节点状态变更事件。"""

    stage: str  # 节点标识（classify / rewrite / retrieve / rerank / generate）
    message: str  # 前端展示的状态描述文本


@dataclass
class SSETokenEvent:
    """LLM 输出 token 事件。"""

    token: str  # LLM 生成的文本片段


@dataclass
class SSECitationEvent:
    """引用来源事件。"""

    source: str  # 文档来源名称
    page: int  # 页码
    snippet: str  # 内容摘要（前 200 字）
    score: float = 0.0  # Reranker 分数
    highlighted_snippet: str | None = None  # 高亮 HTML 片段
    index: int = 0  # 原文档编号（对应 format_context 的 [n]），0 表示兜底无编号


@dataclass
class SSEErrorEvent:
    """错误事件。"""

    error: str  # 错误描述文本


@dataclass
class SSEDoneEvent:
    """流结束事件。"""


@dataclass
class SSEModelInfoEvent:
    """模型信息事件（含 fallback 状态）。"""

    model: str  # 实际使用的模型名
    is_fallback: bool  # 是否触发了 fallback


@dataclass
class SSEAskUserEvent:
    """ask_user 工具推送的问题卡片事件。"""

    type: str = "ask_user"  # 事件类型标识，前端据此路由到追问卡片
    questions: list = field(
        default_factory=list
    )  # [{id, question, options, multi_select}]


@dataclass
class SSEAbstentionEvent:
    """abstention 标识事件 — 检索无达标 context 时提示转人工。"""

    type: str = "abstention"  # 事件类型标识，前端据此路由到转人工提示
    # 转人工提示文案：与 prompts.ABSTENTION_TEXT（"更换问题表述"引导）语义不同，此处引导转人工，故不复用常量
    message: str = "未在文档中找到相关数据，可尝试转人工咨询"


SSEEvent = (
    SSEStatusEvent
    | SSETokenEvent
    | SSECitationEvent
    | SSEErrorEvent
    | SSEDoneEvent
    | SSEModelInfoEvent
    | SSEAskUserEvent  # ask_user 问题卡片
    | SSEAbstentionEvent  # abstention 转人工提示
)


# ── SSE 格式化函数 ─────────────────────────────────


def sse_status(stage: str, message: str, detail: str | None = None) -> str:
    """构建 SSE status 事件。

    Args:
        stage: 阶段标识（retrieving / reranking / generating）
        message: 阶段描述文本
        detail: 可选详细说明

    Returns:
        SSE 格式的文本行
    """
    data: dict[str, str] = {"stage": stage, "message": message}
    if detail:
        data["detail"] = detail
    return f"event: status\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_token(token: str) -> str:
    """构建 SSE token 事件。

    Args:
        token: LLM 生成的文本片段

    Returns:
        SSE 格式的文本行
    """
    return f"event: token\ndata: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"


def sse_citation(
    source: str,
    page: int,
    snippet: str,
    score: float = 0.0,
    highlighted_snippet: str | None = None,
    index: int = 0,
) -> str:
    """构建 SSE citation 事件。

    Args:
        source: 文档来源名称
        page: 页码
        snippet: 内容摘要
        score: Reranker 分数
        highlighted_snippet: 高亮 HTML 片段
        index: 原文档编号（对应 format_context 的 [n]），0 表示无编号

    Returns:
        SSE 格式的文本行
    """
    data = {
        "source": source,
        "page": page,
        "snippet": snippet,
        "score": score,
        "highlighted_snippet": highlighted_snippet,
        "index": index,
    }
    return f"event: citation\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_done() -> str:
    """构建 SSE done 事件（标记流式响应结束）。"""
    return "event: done\ndata: {}\n\n"


def sse_error(error: str) -> str:
    """构建 SSE error 事件。

    Args:
        error: 错误描述文本
    """
    return f"event: error\ndata: {json.dumps({'error': error}, ensure_ascii=False)}\n\n"


def sse_model_info(model: str, is_fallback: bool) -> str:
    """构建 SSE model_info 事件。

    Args:
        model: 实际使用的模型名
        is_fallback: 是否触发了 fallback
    """
    return (
        f"event: model_info\n"
        f"data: {json.dumps({'model': model, 'is_fallback': is_fallback}, ensure_ascii=False)}\n\n"
    )


def sse_ask_user(event: SSEAskUserEvent) -> str:
    """构建 SSE ask_user 事件。

    Args:
        event: 问题卡片事件对象

    Returns:
        SSE 格式的文本行
    """
    data: dict = {
        "type": event.type,
        "questions": event.questions,
    }
    return f"event: ask_user\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_abstention(event: SSEAbstentionEvent) -> str:
    """构建 SSE abstention 事件。

    Args:
        event: abstention 转人工提示事件对象

    Returns:
        SSE 格式的文本行
    """
    data: dict = {
        "type": event.type,
        "message": event.message,
    }
    return f"event: abstention\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def to_sse(event: SSEEvent) -> str:
    """将结构化事件转为 SSE 格式字符串。

    统一调度入口，内部委托给具体的 sse_* 格式化函数。

    Args:
        event: SSEEvent 结构化事件

    Returns:
        SSE 格式的文本行
    """
    match event:
        case SSETokenEvent(token=token):
            return sse_token(token)
        case SSECitationEvent(
            source=s,
            page=p,
            snippet=snippet,
            score=score,
            highlighted_snippet=hs,
            index=idx,
        ):
            return sse_citation(s, p, snippet, score, hs, idx)
        case SSEStatusEvent(stage=stage, message=message):
            return sse_status(stage, message)
        case SSEErrorEvent(error=error):
            return sse_error(error)
        case SSEDoneEvent():
            return sse_done()
        case SSEModelInfoEvent(model=model, is_fallback=is_fallback):
            return sse_model_info(model, is_fallback)
        case SSEAskUserEvent(type=t, questions=questions):
            return sse_ask_user(SSEAskUserEvent(t, questions))
        case SSEAbstentionEvent(type=t, message=message):
            return sse_abstention(SSEAbstentionEvent(t, message))
