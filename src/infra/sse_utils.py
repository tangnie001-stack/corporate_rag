"""SSE (Server-Sent Events) 格式化工具函数。

提供统一的 SSE 事件文本构建函数，供流式聊天端点使用。
所有函数仅依赖标准库 json，无业务依赖。
"""

import json


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
) -> str:
    """构建 SSE citation 事件。

    Args:
        source: 文档来源名称
        page: 页码
        snippet: 内容摘要
        score: Reranker 分数
        highlighted_snippet: 高亮 HTML 片段

    Returns:
        SSE 格式的文本行
    """
    data = {
        "source": source,
        "page": page,
        "snippet": snippet,
        "score": score,
        "highlighted_snippet": highlighted_snippet,
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
