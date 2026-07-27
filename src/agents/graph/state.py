from dataclasses import dataclass, field
from typing import Optional
from src.infra.db.entities import ChunkResult
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
    trace_id: str = field(default_factory=lambda: current_trace_id.get() or "unknown")  # 全链路追踪 ID（自动从 contextvar 读取）
    # ── 中间态 ──
    intent: RAGQueryIntent = field(default_factory=RAGQueryIntent)  # classify_node 输出
    rewritten_query: str = ""  # rewrite_node 改写后的查询
    retrieval_results: list[ChunkResult] = field(default_factory=list)  # 向量/BM25 检索结果
    contexts: list[RAGContext] = field(default_factory=list)  # rerank 精排后的上下文
    grader_score: Optional[float] = None  # grader 关键词覆盖度评分（0~1）
    retrieval_retries: int = 0  # 检索重试次数
    # ── 输出 ──
    answer: str = ""  # LLM 生成的完整回答
    citations: list[dict] = field(default_factory=list)  # 去重引用列表
    # ── 降级控制 ──
    downgraded: bool = False  # 是否降级
    downgrade_reason: str = ""  # 降级原因
    # ── 路由控制 ──
    _resolved_kb_ids: list[str] | None = None
    # None = 未路由 / 降级全量；[...] = 路由选中的 KB ID 列表
    # ── 内部 ──
    _history: list[ChatMessage] = field(default_factory=list)  # 对话历史（注入 prompt 用）
    _token_usage: dict = field(default_factory=dict)  # token 用量统计
    timings: dict = field(default_factory=dict)  # 各节点耗时统计

    @classmethod
    def make_initial_state(cls, session_id, kb_id, query, history):
        """创建图初始状态，只设输入字段，中间态/输出由各节点填充。"""
        return cls(session_id=session_id, kb_id=kb_id, query=query,
                   _history=history)
