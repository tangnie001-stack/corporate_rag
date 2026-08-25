from dataclasses import dataclass, field
from typing import Annotated, Literal

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from src.config.const import MAX_AGENT_ITERATIONS
from src.infra.db.vector_store.types import ChunkResult
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.trace_context import current_trace_id
from src.rag.context import RAGContext


@dataclass
class RAGQueryIntent:
    """查询意图分类结果。"""

    route: str = ""  # "simple" | "medium" | "complex" | ""
    rewritten: bool = False  # 是否已被 rewrite_node 改写


@dataclass
class AgentState:
    """LangGraph 图执行状态。"""

    # ── 输入 ──
    session_id: str = ""  # 会话 ID（多轮对话用，作为 Redis key 取历史）
    kb_id: str = ""  # 知识库 ID（空字符串 = 跨库搜索）
    query: str = ""  # 用户原始查询文本
    trace_id: str = field(
        default_factory=lambda: current_trace_id.get() or "unknown"
    )  # 全链路追踪 ID（自动从 contextvar 读取）
    # ── agent 循环 ──
    messages: Annotated[list[BaseMessage], add_messages] = field(
        default_factory=list
    )  # 模型可见消息（来源：agent 循环节点追加；范围：整轮执行；用途：LLM 上下文，add_messages 提供追加语义）
    tool_contexts: list[RAGContext] = field(
        default_factory=list
    )  # retrieve_kb 累积的检索上下文（来源：检索节点写入；范围：整轮执行；用途：引用溯源）
    _agent_iterations: int = (
        0  # 循环迭代计数（来源：agent 节点自增；范围：单轮执行；用途：调试与护栏判断）
    )
    _max_agent_iterations: int = MAX_AGENT_ITERATIONS  # 迭代上限（来源：src/config/const.py；用途：超限强制收尾）
    _ask_count: int = 0  # 本 turn ask_user 调用次数（来源：ask_user 节点自增；范围：单 turn；用途：日志/兜底，实际检查走 contextvar）
    # ── 中间态 ──
    intent: RAGQueryIntent = field(default_factory=RAGQueryIntent)  # classify_node 输出
    rewritten_query: str = ""  # rewrite_node 改写后的主查询（列表首个）
    rewritten_queries: list[str] = field(
        default_factory=list
    )  # rewrite_node 输出的检索查询列表（含原 query）
    retrieval_results: list[ChunkResult] = field(
        default_factory=list
    )  # 向量/BM25 检索结果
    contexts: list[RAGContext] = field(default_factory=list)  # rerank 精排后的上下文
    # ── 输出 ──
    answer: str = ""  # LLM 生成的完整回答
    citations: list[dict] = field(default_factory=list)  # 去重引用列表
    # ── 模型信息 ──
    model_used: str = ""  # LiteLLM 实际使用的模型名（用于识别 fallback）
    is_fallback: bool = False  # 是否触发了模型 fallback
    # ── 路由控制 ──
    _resolved_kb_ids: list[str] | None = None
    # None = 未路由 / 降级全量；[...] = 路由选中的 KB ID 列表
    # ── 意图理解 ──
    extracted_entities: list[dict] = field(default_factory=list)  # EntityExtractor 输出
    missing_entities: list[dict] = field(
        default_factory=list
    )  # LLM 标记的缺失实体（如 [{"type": "year", "question": "哪一年？"}]
    classification_confidence: float = 0.0  # LLM 置信度（LLM 输出 key="confidence"）
    skip_retrieval: bool = (
        False  # 问候/闲聊标记：跳过检索直接回答（由 classify_node 设置）
    )
    skip_clarify: bool = (
        False  # 评估模式标记：即使缺实体也不进 clarify 追问分支（由评估脚本设置）
    )
    _kb_entities: str = (
        ""  # KB 聚合候选实体文本（classify_node 注入 classifier prompt）
    )
    _kb_suggestions: dict = field(
        default_factory=dict
    )  # KB 聚合候选生成的澄清建议映射（agent_service 消费）
    # ── 内部 ──
    _history: list[ChatMessage] = field(
        default_factory=list
    )  # 对话历史（注入 prompt 用）
    _token_usage: dict = field(default_factory=dict)  # token 用量统计
    timings: dict = field(default_factory=dict)  # 各节点耗时统计

    @classmethod
    def make_initial_state(cls, session_id, kb_id, query, history):
        """创建图初始状态，只设输入字段，中间态/输出由各节点填充。"""
        return cls(session_id=session_id, kb_id=kb_id, query=query, _history=history)


class LangGraphNode:
    """StateGraph 节点名称与输出字段。

    每个节点一个嵌套类，NAME 为节点注册名，其余为该节点输出字段 key。
    与 AgentState 字段同源：生产侧（nodes/query_router）与消费侧
    （agent_service）共用同一套 key，避免字段名散落成两套。
    """

    class KbRouter:
        NAME: str = "kb_router"  # 知识库路由（按 user_id 分发到对应知识库）

    class Classify:
        NAME: str = "classify"  # 查询分类
        INTENT: str = "intent"  # 查询意图（RAGQueryIntent 路由结果）
        EXTRACTED_ENTITIES: str = "extracted_entities"  # 正则提取的实体列表
        MISSING_ENTITIES: str = "missing_entities"  # LLM 标记的缺失实体
        CLASSIFICATION_CONFIDENCE: str = "classification_confidence"  # LLM 置信度
        SKIP_RETRIEVAL: str = "skip_retrieval"  # 问候/闲聊标记：跳过检索直接回答
        KB_ENTITIES: str = "_kb_entities"  # KB 聚合候选实体文本（prompt 注入）
        KB_SUGGESTIONS: str = "_kb_suggestions"  # KB 聚合候选生成的澄清建议映射

    class Rewrite:
        NAME: str = "rewrite"  # 查询改写
        REWRITTEN_QUERY: str = "rewritten_query"  # 改写后主查询（列表首个）
        REWRITTEN_QUERIES: str = "rewritten_queries"  # 检索查询列表（含原 query）

    class Retrieve:
        NAME: str = "retrieve"  # 文档检索

    class Rerank:
        NAME: str = "rerank"  # 重排序
        CONTEXTS: str = "contexts"  # 精排后的上下文列表

    class Generate:
        NAME: str = "generate"  # LLM 生成回答

    class Format:
        NAME: str = "format"  # 引用格式化
        CITATIONS: str = "citations"  # 引用列表输出字段


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
