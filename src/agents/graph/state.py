from dataclasses import dataclass, field
from typing import Literal

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
    # ── 中间态 ──
    intent: RAGQueryIntent = field(default_factory=RAGQueryIntent)  # classify_node 输出
    rewritten_query: str = ""  # rewrite_node 改写后的查询
    retrieval_results: list[ChunkResult] = field(
        default_factory=list
    )  # 向量/BM25 检索结果
    contexts: list[RAGContext] = field(default_factory=list)  # rerank 精排后的上下文
    grader_score: float | None = None  # grader 关键词覆盖度评分（0~1）
    retrieval_retries: int = 0  # 检索重试次数
    # ── 输出 ──
    answer: str = ""  # LLM 生成的完整回答
    citations: list[dict] = field(default_factory=list)  # 去重引用列表
    # ── 降级控制 ──
    downgraded: bool = False  # 是否降级
    downgrade_reason: str = ""  # 降级原因
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
    # ── 内部 ──
    _history: list[ChatMessage] = field(
        default_factory=list
    )  # 对话历史（注入 prompt 用）
    _prev_rewritten_query: str = ""  # 上一轮改写查询（grader 短路重试用，空串=首轮）
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
        PREV_REWRITTEN_QUERY: str = (
            "_prev_rewritten_query"  # 上一轮改写查询（短路判断用）
        )

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

# ── 降级原因（grader 短路 / 重试耗尽） ──
DOWNGRADE_REASON_REWRITE_NO_INCREMENT: str = (
    "rewrite_no_increment"  # rewrite 无信息增量，重试必复现失败，短路降级
)
