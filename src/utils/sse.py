"""SSE (Server-Sent Events) 格式化工具函数。

提供统一的 SSE 事件文本构建函数，供流式聊天端点使用。
仅依赖标准库 json 与 src.config.const 常量（无业务依赖）。
"""

import json
from dataclasses import dataclass, field

from src.config.const import SSEInteractionTexts

# ── 结构化事件 dataclass ─────────────────────────────────


@dataclass
class SSEStatusEvent:
    """图节点状态变更事件。"""

    stage: str  # 节点标识（classify / rewrite / retrieve / rerank / generate）
    message: str  # 前端展示的状态描述文本
    detail: str | None = None  # 可选详细说明（sse_status 的 detail，仅非空时序列化）
    type: str = "status"  # SSE 事件名（event: status）

    def payload_for_buffer(self) -> dict:
        """返回与 to_sse 的 data: 同构的缓冲 payload（含可选 detail）。"""
        data = {"stage": self.stage, "message": self.message}
        if self.detail:
            data["detail"] = self.detail
        return data


@dataclass
class SSETokenEvent:
    """LLM 输出 token 事件。"""

    token: str  # LLM 生成的文本片段
    type: str = "token"  # SSE 事件名（event: token）

    def payload_for_buffer(self) -> dict:
        """返回与 to_sse 的 data: 同构的缓冲 payload。"""
        return {"token": self.token}


@dataclass
class SSECitationEvent:
    """引用来源事件。"""

    source: str  # 文档来源名称
    page: int  # 页码
    snippet: str  # 内容摘要（前 200 字）
    score: float = 0.0  # Reranker 分数
    highlighted_snippet: str | None = None  # 高亮 HTML 片段
    index: int = 0  # 原文档编号（对应 format_context 的 [n]），0 表示兜底无编号
    kind: str = (
        SSEInteractionTexts.CITATION_KIND_KB
    )  # 引用来源类型：kb（知识库文档） / web（网络搜索）
    type: str = "citation"  # SSE 事件名（event: citation）

    def payload_for_buffer(self) -> dict:
        """返回与 to_sse 的 data: 同构的缓冲 payload。"""
        return {
            "source": self.source,
            "page": self.page,
            "snippet": self.snippet,
            "score": self.score,
            "highlighted_snippet": self.highlighted_snippet,
            "index": self.index,
            "kind": self.kind,
        }


@dataclass
class SSEErrorEvent:
    """错误事件。"""

    error: str  # 错误描述文本
    type: str = "error"  # SSE 事件名（event: error）

    def payload_for_buffer(self) -> dict:
        """返回与 to_sse 的 data: 同构的缓冲 payload。"""
        return {"error": self.error}


@dataclass
class SSEDoneEvent:
    """流结束事件。"""

    trace_id: str = ""  # 全链路追踪 ID（前端收到 done 时记录，随答案反馈回传）
    type: str = "done"  # SSE 事件名（event: done）

    def payload_for_buffer(self) -> dict:
        """返回与 to_sse 的 data: 同构的缓冲 payload。"""
        return {"trace_id": self.trace_id}


@dataclass
class SSEModelInfoEvent:
    """模型信息事件（含 fallback 状态）。"""

    model: str  # 实际使用的模型名
    is_fallback: bool  # 是否触发了 fallback
    type: str = "model_info"  # SSE 事件名（event: model_info）

    def payload_for_buffer(self) -> dict:
        """返回与 to_sse 的 data: 同构的缓冲 payload。"""
        return {"model": self.model, "is_fallback": self.is_fallback}


@dataclass
class SSEAskUserEvent:
    """ask_user 工具推送的问题卡片事件。"""

    type: str = "ask_user"  # 事件类型标识，前端据此路由到追问卡片
    questions: list = field(
        default_factory=list
    )  # [{id, question, options, multi_select}]

    def payload_for_buffer(self) -> dict:
        """返回与 to_sse 的 data: 同构的缓冲 payload。"""
        return {"type": self.type, "questions": self.questions}


@dataclass
class SSEAbstentionEvent:
    """abstention 标识事件 — 检索无达标 context 时提示转人工。"""

    type: str = "abstention"  # 事件类型标识，前端据此路由到转人工提示
    # 转人工提示文案：检索无达标 context 时引导用户转人工（与 format_node 的
    # ABSTENTION_MARKERS 拒答检测配套）
    message: str = "未在文档中找到相关数据，可尝试转人工咨询"

    def payload_for_buffer(self) -> dict:
        """返回与 to_sse 的 data: 同构的缓冲 payload。"""
        return {"type": self.type, "message": self.message}


@dataclass
class SSEReasoningDeltaEvent:
    """LLM 思考过程增量事件。"""

    reasoning_delta: str  # 思考文本增量片段（前端累积渲染 Think 行）
    type: str = "reasoning"  # SSE 事件名（event: reasoning）

    def payload_for_buffer(self) -> dict:
        """返回与 to_sse 的 data: 同构的缓冲 payload。"""
        return {"delta": self.reasoning_delta}


SSEEvent = (
    SSEStatusEvent
    | SSETokenEvent
    | SSECitationEvent
    | SSEErrorEvent
    | SSEDoneEvent
    | SSEModelInfoEvent
    | SSEAskUserEvent  # ask_user 问题卡片
    | SSEAbstentionEvent  # abstention 转人工提示
    | SSEReasoningDeltaEvent  # reasoning 思考增量
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
    kind: str = SSEInteractionTexts.CITATION_KIND_KB,
) -> str:
    """构建 SSE citation 事件。

    Args:
        source: 文档来源名称
        page: 页码
        snippet: 内容摘要
        score: Reranker 分数
        highlighted_snippet: 高亮 HTML 片段
        index: 原文档编号（对应 format_context 的 [n]），0 表示无编号
        kind: 引用来源类型：kb（知识库文档） / web（网络搜索）

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
        "kind": kind,
    }
    return f"event: citation\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_done(trace_id: str = "") -> str:
    """构建 SSE done 事件（标记流式响应结束，携带 trace_id 供前端反馈还原链路）。

    Args:
        trace_id: 全链路追踪 ID（空串 = 未捕获到，前端忽略）
    """
    return f"event: done\ndata: {json.dumps({'trace_id': trace_id}, ensure_ascii=False)}\n\n"


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


def sse_reasoning_delta(reasoning_delta: str) -> str:
    """构建 reasoning 事件的 SSE 文本。

    Args:
        reasoning_delta: 思考文本增量片段

    Returns:
        SSE 格式文本（event: reasoning）
    """
    data: dict = {"delta": reasoning_delta}
    return f"event: reasoning\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
            kind=k,
        ):
            return sse_citation(s, p, snippet, score, hs, idx, k)
        case SSEStatusEvent(stage=stage, message=message, detail=detail):
            return sse_status(stage, message, detail)
        case SSEErrorEvent(error=error):
            return sse_error(error)
        case SSEDoneEvent(trace_id=trace_id):
            return sse_done(trace_id)
        case SSEModelInfoEvent(model=model, is_fallback=is_fallback):
            return sse_model_info(model, is_fallback)
        case SSEAskUserEvent(type=t, questions=questions):
            return sse_ask_user(SSEAskUserEvent(t, questions))
        case SSEAbstentionEvent(type=t, message=message):
            return sse_abstention(SSEAbstentionEvent(t, message))
        case SSEReasoningDeltaEvent(reasoning_delta=delta):
            return sse_reasoning_delta(delta)


def from_payload(etype: str, payload: dict) -> "SSEEvent":
    """由缓冲 payload 还原 SSE 事件（resume 回放用）。

    Args:
        etype: SSE 事件名（与 to_sse 输出的 event: 行一致）
        payload: 缓冲 payload（与 to_sse 序列化的 data: 内容同构）

    Returns:
        还原的 SSE 事件对象

    Raises:
        ValueError: 未知事件类型
    """
    if etype == "token":
        return SSETokenEvent(token=payload["token"])
    if etype == "status":
        return SSEStatusEvent(
            stage=payload["stage"],
            message=payload["message"],
            detail=payload.get("detail"),
        )
    if etype == "citation":
        return SSECitationEvent(
            source=payload["source"],
            page=payload["page"],
            snippet=payload["snippet"],
            score=payload["score"],
            highlighted_snippet=payload["highlighted_snippet"],
            index=payload["index"],
            kind=payload["kind"],
        )
    if etype == "done":
        return SSEDoneEvent(trace_id=payload.get("trace_id", ""))
    if etype == "error":
        return SSEErrorEvent(error=payload["error"])
    if etype == "ask_user":
        return SSEAskUserEvent(type=payload["type"], questions=payload["questions"])
    if etype == "abstention":
        return SSEAbstentionEvent(type=payload["type"], message=payload["message"])
    if etype == "reasoning":
        return SSEReasoningDeltaEvent(reasoning_delta=payload["delta"])
    if etype == "model_info":
        return SSEModelInfoEvent(
            model=payload["model"], is_fallback=payload["is_fallback"]
        )
    raise ValueError(f"unknown sse event type: {etype}")
