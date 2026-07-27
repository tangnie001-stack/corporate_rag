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

# ── LangGraph 事件与节点名称 ──


class LangGraph:
    """astream_events 事件类型与节点名称常量。"""

    # 流式事件类型（完整列表，未使用的已注释作为参考）
    CHAIN_START: str = "on_chain_start"  # 节点/链开始执行
    CHAIN_END: str = "on_chain_end"  # 节点/链执行完毕
    # CHAIN_STREAM: str = "on_chain_stream"  # 节点/链产出中间结果
    CHAT_MODEL_STREAM: str = "on_chat_model_stream"  # LLM 流式输出 token
    # CHAT_MODEL_START: str = "on_chat_model_start"  # LLM 开始调用
    # CHAT_MODEL_END: str = "on_chat_model_end"  # LLM 调用结束
    # TOOL_START: str = "on_tool_start"  # 工具调用开始
    # TOOL_END: str = "on_tool_end"  # 工具调用结束
    # TOOL_ERROR: str = "on_tool_error"  # 工具调用异常
    # RETRIEVER_START: str = "on_retriever_start"  # 检索器开始
    # RETRIEVER_END: str = "on_retriever_end"  # 检索器结束
    # RETRIEVER_ERROR: str = "on_retriever_error"  # 检索器异常
    # PARALLEL_START: str = "on_parallel_start"  # 并行分支开始
    # PARALLEL_END: str = "on_parallel_end"  # 并行分支结束

    # 节点名称（与 workflow.py 注册的节点名一致）
    NODE_CLASSIFY: str = "classify"  # 查询分类
    NODE_REWRITE: str = "rewrite"  # 查询改写
    NODE_RETRIEVE: str = "retrieve"  # 文档检索
    NODE_GRADER: str = "grader"  # 检索质量评分
    NODE_RERANK: str = "rerank"  # 重排序
    NODE_GENERATE: str = "generate"  # LLM 生成回答
    NODE_FORMAT: str = "format"  # 引用格式化

    # astream_events API 版本（与 LangGraph compile 的 version 参数类型对齐）
    VERSION: Literal["v1", "v2"] = "v2"  # 当前 LangGraph 稳定版本


# ── SSE 状态事件映射（节点名 → 前端状态提示） ──
# 与 LangGraph.NODE_* 保持一致，不引入额外分词差异
SSE_STATUS: dict[str, str] = {
    LangGraph.NODE_CLASSIFY: "正在分析查询类型...",
    LangGraph.NODE_REWRITE: "正在优化查询...",
    LangGraph.NODE_RETRIEVE: "正在检索相关文档...",
    LangGraph.NODE_RERANK: "正在精排结果...",
    LangGraph.NODE_GENERATE: "正在生成回答...",
}
