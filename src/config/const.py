"""诊断日志标签、LangGraph 事件/节点常量、SSE 状态提示。"""

from typing import Literal


class _Labels(dict):
    """标签字典，get() 无值时返回空字符串。"""

    def get(self, key, *args):
        return super().get(key, args[0] if args else "")


# ── 日志标签字典 ──
ROUTE_LABELS = _Labels({
    "simple": "skip_retrieval",
    "medium": "go_to_rewrite",
    "complex": "go_to_rewrite",
})

GENERATE_LABELS = _Labels({
    True: "fallback_to_naive_rag",
    False: "enhanced_rag",
})

# ── LangGraph 事件类型 ──


class LangGraphEvent:
    """astream_events 事件类型。"""

    CHAIN_START: str = "on_chain_start"  # 节点/链开始执行
    CHAIN_END: str = "on_chain_end"  # 节点/链执行完毕
    CHAIN_STREAM: str = "on_chain_stream"  # 节点/链产出中间结果
    CHAT_MODEL_STREAM: str = "on_chat_model_stream"  # LLM 流式输出 token
    CHAT_MODEL_START: str = "on_chat_model_start"  # LLM 开始调用
    CHAT_MODEL_END: str = "on_chat_model_end"  # LLM 调用结束
    TOOL_START: str = "on_tool_start"  # 工具调用开始
    TOOL_END: str = "on_tool_end"  # 工具调用结束
    TOOL_ERROR: str = "on_tool_error"  # 工具调用异常
    RETRIEVER_START: str = "on_retriever_start"  # 检索器开始
    RETRIEVER_END: str = "on_retriever_end"  # 检索器结束
    RETRIEVER_ERROR: str = "on_retriever_error"  # 检索器异常
    PARALLEL_START: str = "on_parallel_start"  # 并行分支开始
    PARALLEL_END: str = "on_parallel_end"  # 并行分支结束


class LangGraphKey:
    """astream_events 事件 dict 字段 key。"""

    EVENT: str = "event"  # 事件类型字段
    NAME: str = "name"  # 节点名称字段
    DATA: str = "data"  # 事件数据字段
    CHUNK: str = "chunk"  # LLM 流式 chunk（在 data 内）
    OUTPUT: str = "output"  # 节点输出（在 data 内）


class LangGraphNode:
    """StateGraph 节点名称与输出字段。

    每个节点一个嵌套类，NAME 为节点注册名，其余为该节点输出字段 key。
    """

    class Classify:
        NAME: str = "classify"  # 查询分类

    class Rewrite:
        NAME: str = "rewrite"  # 查询改写

    class Retrieve:
        NAME: str = "retrieve"  # 文档检索

    class Grader:
        NAME: str = "grader"  # 检索质量评分
        SCORE: str = "grader_score"  # 关键词覆盖度评分（0~1）
        RETRIEVAL_RETRIES: str = "retrieval_retries"  # 检索重试次数
        DOWNGRADED: str = "downgraded"  # 是否降级
        DOWNGRADE_REASON: str = "downgrade_reason"  # 降级原因

    class Rerank:
        NAME: str = "rerank"  # 重排序
        CONTEXTS: str = "contexts"  # 精排后的上下文列表

    class Generate:
        NAME: str = "generate"  # LLM 生成回答

    class Format:
        NAME: str = "format"  # 引用格式化


class LangGraph:
    """astream_events API 版本。"""

    VERSION: Literal["v1", "v2"] = "v2"  # 当前 LangGraph 稳定版本


# ── SSE 状态事件映射（节点名 → 前端状态提示） ──
# 与 LangGraphNode.* 保持一致，不引入额外分词差异
SSE_STATUS: dict[str, str] = {
    LangGraphNode.Classify.NAME: "正在分析查询类型...",
    LangGraphNode.Rewrite.NAME: "正在优化查询...",
    LangGraphNode.Retrieve.NAME: "正在检索相关文档...",
    LangGraphNode.Rerank.NAME: "正在精排结果...",
    LangGraphNode.Generate.NAME: "正在生成回答...",
}
