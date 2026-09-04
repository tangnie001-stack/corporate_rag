"""统一事件定义层 — EventSpec 注册表 / Event 枚举 / Signal 枚举 / ReplayEvent。

单一事实源：分层前缀允许集、信号类型、普通事件名与级别均在此定义并在 import 期校验。
src/core/logging.py 的 helper 只从本模块读取，禁止在别处硬编码前缀/事件字符串。

规范来源：docs/agents/logging-rules.md（唯一归属文档）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# 分层前缀允许集（logging-rules.md 前缀主表：6 处理层 + cli + app）
LOG_PREFIXES: frozenset[str] = frozenset(
    {"retrieval", "verify", "agent", "session", "db", "llm", "cli", "app"}
)

# 信号行保留前缀（P1 Change 2 契约，独立于 [层] 前缀的已知例外）
SIGNAL_PREFIX = "retrieval_signal:"

_ALLOWED_LEVELS = frozenset({"info", "warning", "error"})


class Signal(str, Enum):
    """检索行为信号类型（retrieval_signal 行的 signal= 值）。"""

    RERETRIEVE = "reretrieve"
    TO_WEB = "to_web"
    ABSTAIN_AFTER_RETRIEVE = "abstain_after_retrieve"
    UNSUPPORTED = "unsupported"
    CITED = "cited"
    EMPTY_RESULT = "empty_result"


class Event(str, Enum):
    """普通事件枚举，值 = 事件名（log_event 的事件 key）。

    新增事件两步：1) 在此加成员；2) 在 EVENT_SPECS 登记对应 EventSpec。
    二者不一致会在本模块 import 时抛 AssertionError（import 期一致性校验）。
    事件名遵循 logging-rules.md：英文小写空格分隔、禁用函数/工具下划线名。
    """

    # [retrieval] 核心里程碑（3.1 试点存量，Task 3 转换调用点）
    SEARCH_DONE = "search done"
    HYBRID_DONE = "hybrid done"
    RERANK_SKIP = "rerank skip"
    RERANK_DONE = "rerank done"
    RERANK_FAILED = "rerank failed"
    RERANK_TIMEOUT = "rerank timeout"
    RETRIEVE_DONE = "retrieve done"
    WEB_SEARCH_DONE = "web search done"
    RETRIEVE_REPLAY = "retrieve replay"

    # [agent] agent 循环 / 图 / 编排事件（3.3 批迁移登记）
    ITERATION_DONE = "iteration done"
    ITERATION_LIMIT = "iteration limit"
    MODEL_TURN = "model turn"
    GRAPH_COMPILED = "graph compiled"
    FORMAT_DONE = "format done"
    EVENT_CONVERT_FAILED = "event convert failed"
    SERVICE_READY = "service ready"

    # [verify] 验证管道事件（3.2 批迁移登记：态A 引用引导 / 态B 完整性+judge）
    SKIP = "skip"
    COMPLETENESS_CHECK = "completeness check"
    JUDGE_START = "judge start"
    JUDGE_DONE = "judge done"
    JUDGE_FAILED = "judge failed"
    WEB_CONFIRM_ASK = "web confirm ask"
    WEB_CONFIRM_RESULT = "web confirm result"
    REGEN_STOP = "regen stop"
    CITATION_GUIDE = "citation guide"

    # [session] 会话管理事件（3.4 批迁移登记：chat manager / persistence / streaming）
    REDIS_READY = "redis ready"
    REDIS_FALLBACK = "redis fallback"
    HISTORY_WRITE_FAILED = "history write failed"
    HISTORY_READ_FAILED = "history read failed"
    HISTORY_CLEAR_FAILED = "history clear failed"
    SESSION_SAVE_FAILED = "session save failed"
    MESSAGE_SAVE_FAILED = "message save failed"
    TASK_REGISTERED = "task registered"

    # [db] 数据库层事件（3.4 批迁移登记：vector_store / file_store）
    CHROMA_CLIENT_READY = "chroma client ready"
    BUCKET_CREATED = "bucket created"
    FILE_UPLOAD_FAILED = "file upload failed"
    FILE_DOWNLOAD_FAILED = "file download failed"
    FILE_DELETE_FAILED = "file delete failed"
    CHUNKS_ADDED = "chunks added"
    CHUNKS_DELETED = "chunks deleted"
    CHUNKS_READ = "chunks read"
    CHUNKS_READ_FAILED = "chunks read failed"
    COLLECTION_DELETED = "collection deleted"
    COLLECTION_DELETE_FAILED = "collection delete failed"
    SEARCH_STATS = "search stats"
    SEARCH_RESULT = "search result"
    SEARCH_ALL_DONE = "search all done"
    SEARCH_COLLECTION_FAILED = "search collection failed"

    # [llm] LLM 调用层事件（3.4 批迁移登记：langfuse / prompt / LLM 内容深日志）
    TRACE_READY = "trace ready"
    TRACE_INIT_FAILED = "trace init failed"
    TRACE_SKIP = "trace skip"
    PROMPT_FETCHED = "prompt fetched"
    PROMPT_FETCH_FAILED = "prompt fetch failed"
    PROMPT_FALLBACK = "prompt fallback"
    CONTENT_START = "content start"
    CONTENT_END = "content end"

    # [cli] 离线工具事件（3.5 批迁移登记：eval_ragas / eval_ragas_generate /
    # rebuild_bm25 / check_retrieval / compare_rewrite）
    TESTSET_LOADED = "testset loaded"
    EVALUATION_RUN = "evaluation run"
    EVALUATION_START = "evaluation start"
    EVALUATION_DONE = "evaluation done"
    EVAL_MODEL_INIT = "eval model init"
    RAG_COMPONENT_INIT = "rag component init"
    VECTOR_STORE_CHECK = "vector store check"
    VECTOR_STORE_EMPTY = "vector store empty"
    RAGAS_MODEL_MISSING = "ragas model missing"
    ANSWERS_GENERATION = "answers generation"
    QA_ANSWER_START = "question answer start"
    QA_ANSWER_DONE = "question answer done"
    QA_ANSWER_FAILED = "question answer failed"
    METRIC_MEAN = "metric mean"
    METRICS_NAN = "metrics nan"
    RESULTS_SAVED = "results saved"
    REPORT_SAVED = "report saved"
    EVAL_REPORT_SAVED = "eval report saved"
    EVAL_SAVE_FAILED = "eval save failed"
    VERTEXAI_STUB_CREATED = "vertexai stub created"
    QUESTION_PROOFREAD_FAILED = "question proofread failed"
    QUESTION_PROOFREAD_DONE = "question proofread done"
    KB_META_FAILED = "kb meta failed"
    CHUNK_LOAD_START = "chunk load start"
    CHUNKS_NOT_FOUND = "chunks not found"
    NO_CHUNK_DATA = "no chunk data"
    CHUNK_LOAD_DONE = "chunk load done"
    GENERATOR_INIT = "generator init"
    TRANSFORMS_SETUP = "transforms setup"
    KNOWLEDGE_GRAPH_FOUND = "knowledge graph found"
    KNOWLEDGE_GRAPH_BUILD = "knowledge graph build"
    KNOWLEDGE_GRAPH_SAVED = "knowledge graph saved"
    TESTSET_GENERATION_START = "testset generation start"
    TESTSET_SAVED = "testset saved"
    BM25_REBUILD_SKIP = "bm25 rebuild skip"
    KBS_FOUND = "kbs found"
    KB_CHUNKS_MISSING = "kb chunks missing"
    BM25_REBUILT = "bm25 rebuilt"
    BM25_REBUILD_DONE = "bm25 rebuild done"
    KB_LOOKUP = "kb lookup"
    KB_NOT_FOUND = "kb not found"
    SEARCH_START = "search start"
    SEARCH_FAILED = "search failed"
    LLM_INVOKE_FAILED = "llm invoke failed"
    LLM_PARSE_FAILED = "llm parse failed"
    RERANK_ERROR = "rerank error"

    # [app] 应用边界事件（3.5 批迁移登记：main.py 生命周期 + 全局异常兜底）
    APP_STARTING = "app starting"
    APP_STOPPING = "app stopping"
    CHROMA_WARMUP_DONE = "chroma warmup done"
    CHROMA_WARMUP_FAILED = "chroma warmup failed"
    STALE_LOCKS_CLEARED = "stale locks cleared"
    STALE_LOCKS_CLEAR_FAILED = "locks clear failed"
    BIZ_ERROR = "biz error"
    HTTP_ERROR = "http error"
    VALIDATION_ERROR = "validation error"
    EXCEPTION_CHAIN = "exception chain"


@dataclass(frozen=True)
class EventSpec:
    """普通事件描述（注册表条目）。

    name: 事件名（Event 成员的值，注册表 key）
    prefix: 分层前缀（须在 LOG_PREFIXES 内）
    level: 事件标准级别（helper 按此路由 logger 级别）
    fields: 字段名登记（供 review / 文档对照，不参与运行时校验）
    """

    name: str
    prefix: str
    level: str
    fields: tuple[str, ...] = ()


EVENT_SPECS: dict[str, EventSpec] = {
    Event.SEARCH_DONE.value: EventSpec(
        Event.SEARCH_DONE.value,
        "retrieval",
        "info",
        ("kb_id", "query_len", "result_count"),
    ),
    Event.HYBRID_DONE.value: EventSpec(
        Event.HYBRID_DONE.value,
        "retrieval",
        "info",
        ("kb_id", "query_len", "result_count"),
    ),
    Event.RERANK_SKIP.value: EventSpec(
        Event.RERANK_SKIP.value, "retrieval", "info", ("reason",)
    ),
    Event.RERANK_DONE.value: EventSpec(
        Event.RERANK_DONE.value,
        "retrieval",
        "info",
        ("doc_count", "query_len"),
    ),
    Event.RERANK_FAILED.value: EventSpec(
        Event.RERANK_FAILED.value,
        "retrieval",
        "warning",
        ("attempts", "query", "err"),
    ),
    Event.RERANK_TIMEOUT.value: EventSpec(
        Event.RERANK_TIMEOUT.value,
        "retrieval",
        "warning",
        ("timeout_s", "query"),
    ),
    Event.RETRIEVE_DONE.value: EventSpec(
        Event.RETRIEVE_DONE.value,
        "retrieval",
        "info",
        ("iteration", "query", "result_count", "latency_ms"),
    ),
    Event.WEB_SEARCH_DONE.value: EventSpec(
        Event.WEB_SEARCH_DONE.value,
        "retrieval",
        "info",
        ("query_count", "result_count", "latency_ms"),
    ),
    Event.RETRIEVE_REPLAY.value: EventSpec(
        Event.RETRIEVE_REPLAY.value,
        "retrieval",
        "info",
        (
            "query",
            "query_len",
            "kb_id",
            "iteration",
            "top_k",
            "dedup_max_per_doc",
            "hybrid",
            "rerank",
        ),
    ),
    # [agent] agent 循环 / 图 / 编排（3.3 批）
    Event.ITERATION_DONE.value: EventSpec(
        Event.ITERATION_DONE.value, "agent", "info", ("iteration", "msgs")
    ),
    Event.ITERATION_LIMIT.value: EventSpec(
        Event.ITERATION_LIMIT.value,
        "agent",
        "warning",
        ("query", "iteration"),
    ),
    Event.MODEL_TURN.value: EventSpec(
        Event.MODEL_TURN.value,
        "agent",
        "info",
        (
            "model",
            "usage_in",
            "usage_out",
            "usage_estimated",
            "fallback",
            "latency_ms",
            "iteration",
        ),
    ),
    Event.GRAPH_COMPILED.value: EventSpec(Event.GRAPH_COMPILED.value, "agent", "info"),
    Event.FORMAT_DONE.value: EventSpec(
        Event.FORMAT_DONE.value, "agent", "info", ("citations", "reason")
    ),
    Event.EVENT_CONVERT_FAILED.value: EventSpec(
        Event.EVENT_CONVERT_FAILED.value,
        "agent",
        "warning",
        ("item_type", "err"),
    ),
    Event.SERVICE_READY.value: EventSpec(Event.SERVICE_READY.value, "agent", "info"),
    # [verify] 验证管道（3.2 批）
    Event.SKIP.value: EventSpec(Event.SKIP.value, "verify", "info", ("reason",)),
    Event.COMPLETENESS_CHECK.value: EventSpec(
        Event.COMPLETENESS_CHECK.value,
        "verify",
        "info",
        ("kb_id", "required", "missing", "answer_len"),
    ),
    Event.JUDGE_START.value: EventSpec(Event.JUDGE_START.value, "verify", "info"),
    Event.JUDGE_DONE.value: EventSpec(
        Event.JUDGE_DONE.value,
        "verify",
        "info",
        ("unsupported_count",),
    ),
    Event.JUDGE_FAILED.value: EventSpec(
        Event.JUDGE_FAILED.value,
        "verify",
        "warning",
        ("answer_len", "err"),
    ),
    Event.WEB_CONFIRM_ASK.value: EventSpec(
        Event.WEB_CONFIRM_ASK.value,
        "verify",
        "info",
        ("missing",),
    ),
    Event.WEB_CONFIRM_RESULT.value: EventSpec(
        Event.WEB_CONFIRM_RESULT.value,
        "verify",
        "info",
        ("missing", "confirmed"),
    ),
    Event.REGEN_STOP.value: EventSpec(
        Event.REGEN_STOP.value,
        "verify",
        "info",
        ("reason", "missing", "regenerations"),
    ),
    Event.CITATION_GUIDE.value: EventSpec(
        Event.CITATION_GUIDE.value,
        "verify",
        "info",
        ("kind", "answer_len"),
    ),
    # [session] 会话管理（3.4 批）
    Event.REDIS_READY.value: EventSpec(Event.REDIS_READY.value, "session", "info"),
    Event.REDIS_FALLBACK.value: EventSpec(
        Event.REDIS_FALLBACK.value, "session", "warning", ("err",)
    ),
    Event.HISTORY_WRITE_FAILED.value: EventSpec(
        Event.HISTORY_WRITE_FAILED.value, "session", "warning", ("err",)
    ),
    Event.HISTORY_READ_FAILED.value: EventSpec(
        Event.HISTORY_READ_FAILED.value, "session", "warning", ("err",)
    ),
    Event.HISTORY_CLEAR_FAILED.value: EventSpec(
        Event.HISTORY_CLEAR_FAILED.value, "session", "warning", ("err",)
    ),
    Event.SESSION_SAVE_FAILED.value: EventSpec(
        Event.SESSION_SAVE_FAILED.value, "session", "warning", ("err",)
    ),
    Event.MESSAGE_SAVE_FAILED.value: EventSpec(
        Event.MESSAGE_SAVE_FAILED.value, "session", "warning", ("role", "err")
    ),
    Event.TASK_REGISTERED.value: EventSpec(
        Event.TASK_REGISTERED.value, "session", "info"
    ),
    # [db] 向量检索 / 文件存储（3.4 批）
    Event.CHROMA_CLIENT_READY.value: EventSpec(
        Event.CHROMA_CLIENT_READY.value,
        "db",
        "info",
        ("persist_dir", "model"),
    ),
    Event.BUCKET_CREATED.value: EventSpec(
        Event.BUCKET_CREATED.value, "db", "info", ("bucket",)
    ),
    Event.FILE_UPLOAD_FAILED.value: EventSpec(
        Event.FILE_UPLOAD_FAILED.value, "db", "warning", ("key", "err")
    ),
    Event.FILE_DOWNLOAD_FAILED.value: EventSpec(
        Event.FILE_DOWNLOAD_FAILED.value, "db", "warning", ("key", "err")
    ),
    Event.FILE_DELETE_FAILED.value: EventSpec(
        Event.FILE_DELETE_FAILED.value, "db", "warning", ("key", "err")
    ),
    Event.CHUNKS_ADDED.value: EventSpec(
        Event.CHUNKS_ADDED.value,
        "db",
        "info",
        ("kb_id", "doc_id", "count"),
    ),
    Event.CHUNKS_DELETED.value: EventSpec(
        Event.CHUNKS_DELETED.value, "db", "info", ("doc_id", "count")
    ),
    Event.CHUNKS_READ.value: EventSpec(
        Event.CHUNKS_READ.value,
        "db",
        "info",
        ("doc_id", "kb_id", "page", "total", "count"),
    ),
    Event.CHUNKS_READ_FAILED.value: EventSpec(
        Event.CHUNKS_READ_FAILED.value,
        "db",
        "warning",
        ("doc_id", "kb_id", "err"),
    ),
    Event.COLLECTION_DELETED.value: EventSpec(
        Event.COLLECTION_DELETED.value, "db", "info", ("name",)
    ),
    Event.COLLECTION_DELETE_FAILED.value: EventSpec(
        Event.COLLECTION_DELETE_FAILED.value, "db", "warning", ("name",)
    ),
    Event.SEARCH_STATS.value: EventSpec(
        Event.SEARCH_STATS.value,
        "db",
        "info",
        ("kb_id", "collection_name", "collection_count", "top_k"),
    ),
    Event.SEARCH_RESULT.value: EventSpec(
        Event.SEARCH_RESULT.value,
        "db",
        "info",
        ("kb_id", "query_len", "result_count", "model"),
    ),
    Event.SEARCH_ALL_DONE.value: EventSpec(
        Event.SEARCH_ALL_DONE.value,
        "db",
        "info",
        ("collections", "query_len", "result_count"),
    ),
    Event.SEARCH_COLLECTION_FAILED.value: EventSpec(
        Event.SEARCH_COLLECTION_FAILED.value,
        "db",
        "warning",
        ("kb_id", "err"),
    ),
    # [llm] Langfuse 追踪 / prompt 拉取 / LLM 内容深日志（3.4 批）
    Event.TRACE_READY.value: EventSpec(Event.TRACE_READY.value, "llm", "info"),
    Event.TRACE_INIT_FAILED.value: EventSpec(
        Event.TRACE_INIT_FAILED.value, "llm", "warning", ("err",)
    ),
    Event.TRACE_SKIP.value: EventSpec(
        Event.TRACE_SKIP.value,
        "llm",
        "warning",
        ("stage", "reason"),
    ),
    Event.PROMPT_FETCHED.value: EventSpec(
        Event.PROMPT_FETCHED.value, "llm", "info", ("name", "version")
    ),
    Event.PROMPT_FETCH_FAILED.value: EventSpec(
        Event.PROMPT_FETCH_FAILED.value, "llm", "warning", ("name", "err")
    ),
    Event.PROMPT_FALLBACK.value: EventSpec(
        Event.PROMPT_FALLBACK.value, "llm", "info", ("name",)
    ),
    Event.CONTENT_START.value: EventSpec(
        Event.CONTENT_START.value, "llm", "info", ("model", "prompt")
    ),
    Event.CONTENT_END.value: EventSpec(
        Event.CONTENT_END.value, "llm", "info", ("model", "output")
    ),
    # [cli] 离线工具（3.5 批：eval_ragas / eval_ragas_generate / rebuild_bm25 / check_retrieval / compare_rewrite）
    Event.TESTSET_LOADED.value: EventSpec(
        Event.TESTSET_LOADED.value, "cli", "info", ("count",)
    ),
    Event.EVALUATION_RUN.value: EventSpec(
        Event.EVALUATION_RUN.value, "cli", "info", ("kb_id",)
    ),
    Event.EVALUATION_START.value: EventSpec(
        Event.EVALUATION_START.value, "cli", "info", ("samples",)
    ),
    Event.EVALUATION_DONE.value: EventSpec(Event.EVALUATION_DONE.value, "cli", "info"),
    Event.EVAL_MODEL_INIT.value: EventSpec(
        Event.EVAL_MODEL_INIT.value, "cli", "info", ("sut", "judge")
    ),
    Event.RAG_COMPONENT_INIT.value: EventSpec(
        Event.RAG_COMPONENT_INIT.value, "cli", "info"
    ),
    Event.VECTOR_STORE_CHECK.value: EventSpec(
        Event.VECTOR_STORE_CHECK.value, "cli", "info", ("kb_id",)
    ),
    Event.VECTOR_STORE_EMPTY.value: EventSpec(
        Event.VECTOR_STORE_EMPTY.value, "cli", "error", ("kb_id",)
    ),
    Event.RAGAS_MODEL_MISSING.value: EventSpec(
        Event.RAGAS_MODEL_MISSING.value, "cli", "error"
    ),
    Event.ANSWERS_GENERATION.value: EventSpec(
        Event.ANSWERS_GENERATION.value, "cli", "info", ("count",)
    ),
    Event.QA_ANSWER_START.value: EventSpec(
        Event.QA_ANSWER_START.value, "cli", "info", ("index", "query")
    ),
    Event.QA_ANSWER_DONE.value: EventSpec(
        Event.QA_ANSWER_DONE.value,
        "cli",
        "info",
        ("index", "answer_len", "contexts"),
    ),
    Event.QA_ANSWER_FAILED.value: EventSpec(
        Event.QA_ANSWER_FAILED.value, "cli", "warning", ("index", "err")
    ),
    Event.METRIC_MEAN.value: EventSpec(
        Event.METRIC_MEAN.value, "cli", "info", ("metric", "mean")
    ),
    Event.METRICS_NAN.value: EventSpec(
        Event.METRICS_NAN.value, "cli", "warning", ("counts",)
    ),
    Event.RESULTS_SAVED.value: EventSpec(
        Event.RESULTS_SAVED.value, "cli", "info", ("file",)
    ),
    Event.REPORT_SAVED.value: EventSpec(
        Event.REPORT_SAVED.value, "cli", "info", ("file",)
    ),
    Event.EVAL_REPORT_SAVED.value: EventSpec(
        Event.EVAL_REPORT_SAVED.value, "cli", "info", ("kb_id",)
    ),
    Event.EVAL_SAVE_FAILED.value: EventSpec(
        Event.EVAL_SAVE_FAILED.value, "cli", "warning", ("err",)
    ),
    Event.VERTEXAI_STUB_CREATED.value: EventSpec(
        Event.VERTEXAI_STUB_CREATED.value, "cli", "info", ("file",)
    ),
    Event.QUESTION_PROOFREAD_FAILED.value: EventSpec(
        Event.QUESTION_PROOFREAD_FAILED.value,
        "cli",
        "warning",
        ("question", "err"),
    ),
    Event.QUESTION_PROOFREAD_DONE.value: EventSpec(
        Event.QUESTION_PROOFREAD_DONE.value,
        "cli",
        "info",
        ("cleaned", "total"),
    ),
    Event.KB_META_FAILED.value: EventSpec(
        Event.KB_META_FAILED.value, "cli", "error", ("err",)
    ),
    Event.CHUNK_LOAD_START.value: EventSpec(
        Event.CHUNK_LOAD_START.value, "cli", "info", ("kb_id", "whitelist")
    ),
    Event.CHUNKS_NOT_FOUND.value: EventSpec(
        Event.CHUNKS_NOT_FOUND.value, "cli", "warning", ("doc_id",)
    ),
    Event.NO_CHUNK_DATA.value: EventSpec(Event.NO_CHUNK_DATA.value, "cli", "error"),
    Event.CHUNK_LOAD_DONE.value: EventSpec(
        Event.CHUNK_LOAD_DONE.value, "cli", "info", ("docs", "chunks")
    ),
    Event.GENERATOR_INIT.value: EventSpec(
        Event.GENERATOR_INIT.value,
        "cli",
        "info",
        ("model", "size", "chunks"),
    ),
    Event.TRANSFORMS_SETUP.value: EventSpec(
        Event.TRANSFORMS_SETUP.value, "cli", "info", ("steps",)
    ),
    Event.KNOWLEDGE_GRAPH_FOUND.value: EventSpec(
        Event.KNOWLEDGE_GRAPH_FOUND.value, "cli", "info", ("file",)
    ),
    Event.KNOWLEDGE_GRAPH_BUILD.value: EventSpec(
        Event.KNOWLEDGE_GRAPH_BUILD.value, "cli", "info", ("chunks",)
    ),
    Event.KNOWLEDGE_GRAPH_SAVED.value: EventSpec(
        Event.KNOWLEDGE_GRAPH_SAVED.value, "cli", "info", ("file",)
    ),
    Event.TESTSET_GENERATION_START.value: EventSpec(
        Event.TESTSET_GENERATION_START.value, "cli", "info", ("size",)
    ),
    Event.TESTSET_SAVED.value: EventSpec(
        Event.TESTSET_SAVED.value,
        "cli",
        "info",
        ("file", "count", "version"),
    ),
    Event.BM25_REBUILD_SKIP.value: EventSpec(
        Event.BM25_REBUILD_SKIP.value, "cli", "info"
    ),
    Event.KBS_FOUND.value: EventSpec(Event.KBS_FOUND.value, "cli", "info", ("count",)),
    Event.KB_CHUNKS_MISSING.value: EventSpec(
        Event.KB_CHUNKS_MISSING.value, "cli", "warning", ("kb_id",)
    ),
    Event.BM25_REBUILT.value: EventSpec(
        Event.BM25_REBUILT.value, "cli", "info", ("kb_id", "chunks")
    ),
    Event.BM25_REBUILD_DONE.value: EventSpec(
        Event.BM25_REBUILD_DONE.value,
        "cli",
        "info",
        ("rebuilt", "skipped", "failed"),
    ),
    Event.KB_LOOKUP.value: EventSpec(Event.KB_LOOKUP.value, "cli", "info", ("name",)),
    Event.KB_NOT_FOUND.value: EventSpec(
        Event.KB_NOT_FOUND.value, "cli", "error", ("name",)
    ),
    Event.SEARCH_START.value: EventSpec(
        Event.SEARCH_START.value, "cli", "info", ("query", "top_k")
    ),
    Event.SEARCH_FAILED.value: EventSpec(
        Event.SEARCH_FAILED.value, "cli", "warning", ("query", "err")
    ),
    Event.LLM_INVOKE_FAILED.value: EventSpec(
        Event.LLM_INVOKE_FAILED.value, "cli", "warning", ("err",)
    ),
    Event.LLM_PARSE_FAILED.value: EventSpec(
        Event.LLM_PARSE_FAILED.value, "cli", "warning", ("err", "raw")
    ),
    Event.RERANK_ERROR.value: EventSpec(
        Event.RERANK_ERROR.value, "cli", "warning", ("query", "err")
    ),
    # [app] 应用边界（3.5 批：main.py 生命周期 + 全局异常兜底）
    Event.APP_STARTING.value: EventSpec(Event.APP_STARTING.value, "app", "info"),
    Event.APP_STOPPING.value: EventSpec(Event.APP_STOPPING.value, "app", "info"),
    Event.CHROMA_WARMUP_DONE.value: EventSpec(
        Event.CHROMA_WARMUP_DONE.value, "app", "info", ("collections",)
    ),
    Event.CHROMA_WARMUP_FAILED.value: EventSpec(
        Event.CHROMA_WARMUP_FAILED.value, "app", "warning", ("err",)
    ),
    Event.STALE_LOCKS_CLEARED.value: EventSpec(
        Event.STALE_LOCKS_CLEARED.value, "app", "info", ("count",)
    ),
    Event.STALE_LOCKS_CLEAR_FAILED.value: EventSpec(
        Event.STALE_LOCKS_CLEAR_FAILED.value, "app", "warning", ("err",)
    ),
    Event.BIZ_ERROR.value: EventSpec(
        Event.BIZ_ERROR.value, "app", "error", ("code", "message")
    ),
    Event.HTTP_ERROR.value: EventSpec(
        Event.HTTP_ERROR.value, "app", "error", ("status", "detail")
    ),
    Event.VALIDATION_ERROR.value: EventSpec(
        Event.VALIDATION_ERROR.value, "app", "error", ("errors",)
    ),
    Event.EXCEPTION_CHAIN.value: EventSpec(
        Event.EXCEPTION_CHAIN.value,
        "app",
        "error",
        ("depth", "type", "msg"),
    ),
}


@dataclass(frozen=True)
class ReplayEvent:
    """检索重放上下文（retrieve replay 事件行字段容器）。

    query: 检索词全文（重放输入）
    query_len: 检索词字符数
    kb_id: 知识库 ID（空 = 未绑定）
    iteration: agent 迭代序号
    top_k: 工具层返回上限（精排后截断）
    dedup_max_per_doc: 同文档去重上限（settings.RETRIEVAL_MAX_PER_DOC）
    hybrid: 是否启用混合检索
    rerank: 是否执行精排
    """

    query: str
    query_len: int
    kb_id: str
    iteration: int
    top_k: int
    dedup_max_per_doc: int | None
    hybrid: bool
    rerank: bool


def _validate_registry(events: set[str], specs: dict[str, EventSpec]) -> None:
    """校验 Event 成员集与 EVENT_SPECS 一致且元数据合法（import 期调用）。"""
    if events != set(specs):
        missing = events - set(specs)
        extra = set(specs) - events
        raise AssertionError(
            "Event 成员与 EVENT_SPECS 不一致 "
            f"(缺 spec: {sorted(missing)}; 多余 spec: {sorted(extra)})"
        )
    for name, spec in specs.items():
        if spec.name != name:
            raise AssertionError(f"EventSpec name 与 key 不一致: {spec.name} != {name}")
        if spec.prefix not in LOG_PREFIXES:
            raise AssertionError(
                f"事件 {name} 前缀 {spec.prefix!r} 未在 LOG_PREFIXES 登记"
            )
        if spec.level not in _ALLOWED_LEVELS:
            raise AssertionError(f"事件 {name} 级别非法: {spec.level}")


_validate_registry({e.value for e in Event}, EVENT_SPECS)
