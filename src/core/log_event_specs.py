"""普通事件注册表数据 — EventSpec dataclass + EVENT_SPECS 字面量表。

纯数据模块，自包含（不 import log_events，避免环依赖）。Event 枚举与
import 期一致性校验留在 log_events.py；本表 key/name 为字面字符串，
与 Event 枚举值的一致性由 log_events 的 _validate_registry 在 import 期断言。
"""

from __future__ import annotations

from dataclasses import dataclass


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
    "search done": EventSpec(
        "search done", "retrieval", "info", ("kb_id", "query_len", "result_count")
    ),
    "hybrid done": EventSpec(
        "hybrid done", "retrieval", "info", ("kb_id", "query_len", "result_count")
    ),
    "rerank skip": EventSpec("rerank skip", "retrieval", "info", ("reason",)),
    "rerank done": EventSpec(
        "rerank done", "retrieval", "info", ("doc_count", "query_len")
    ),
    "rerank failed": EventSpec(
        "rerank failed", "retrieval", "warning", ("attempts", "query", "err")
    ),
    "rerank timeout": EventSpec(
        "rerank timeout", "retrieval", "warning", ("timeout_s", "query")
    ),
    "retrieve done": EventSpec(
        "retrieve done",
        "retrieval",
        "info",
        ("iteration", "query", "result_count", "latency_ms"),
    ),
    "web search done": EventSpec(
        "web search done",
        "retrieval",
        "info",
        ("query_count", "result_count", "latency_ms"),
    ),
    "retrieve replay": EventSpec(
        "retrieve replay",
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
    "iteration done": EventSpec(
        "iteration done", "agent", "info", ("iteration", "msgs")
    ),
    "iteration limit": EventSpec(
        "iteration limit", "agent", "warning", ("query", "iteration")
    ),
    "model turn": EventSpec(
        "model turn",
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
    "graph compiled": EventSpec("graph compiled", "agent", "info"),
    "format done": EventSpec("format done", "agent", "info", ("citations", "reason")),
    "event convert failed": EventSpec(
        "event convert failed", "agent", "warning", ("item_type", "err")
    ),
    "service ready": EventSpec("service ready", "agent", "info"),
    "agent bind ignored": EventSpec(
        "agent bind ignored",
        "agent",
        "warning",
        ("session_id", "bound", "requested"),
    ),
    # [verify] 验证管道（3.2 批）
    "skip": EventSpec("skip", "verify", "info", ("reason",)),
    "completeness check": EventSpec(
        "completeness check",
        "verify",
        "info",
        ("kb_id", "required", "missing", "answer_len"),
    ),
    "web confirm ask": EventSpec("web confirm ask", "verify", "info", ("missing",)),
    "web confirm result": EventSpec(
        "web confirm result", "verify", "info", ("missing", "confirmed")
    ),
    "regen stop": EventSpec(
        "regen stop", "verify", "info", ("reason", "missing", "regenerations")
    ),
    "citation guide": EventSpec(
        "citation guide", "verify", "info", ("kind", "answer_len")
    ),
    # [session] 会话管理（3.4 批）
    "redis ready": EventSpec("redis ready", "session", "info"),
    "redis fallback": EventSpec("redis fallback", "session", "warning", ("err",)),
    "history write failed": EventSpec(
        "history write failed", "session", "warning", ("err",)
    ),
    "history read failed": EventSpec(
        "history read failed", "session", "warning", ("err",)
    ),
    "history clear failed": EventSpec(
        "history clear failed", "session", "warning", ("err",)
    ),
    "session save failed": EventSpec(
        "session save failed", "session", "warning", ("err",)
    ),
    "message save failed": EventSpec(
        "message save failed", "session", "warning", ("role", "err")
    ),
    "task registered": EventSpec("task registered", "session", "info"),
    "skill preload skip": EventSpec(
        "skill preload skip", "session", "warning", ("skill", "reason")
    ),
    # [db] 向量检索 / 文件存储（3.4 批）
    "chroma client ready": EventSpec(
        "chroma client ready", "db", "info", ("persist_dir", "model")
    ),
    "bucket created": EventSpec("bucket created", "db", "info", ("bucket",)),
    "file upload failed": EventSpec(
        "file upload failed", "db", "warning", ("key", "err")
    ),
    "file download failed": EventSpec(
        "file download failed", "db", "warning", ("key", "err")
    ),
    "file delete failed": EventSpec(
        "file delete failed", "db", "warning", ("key", "err")
    ),
    "chunks added": EventSpec(
        "chunks added", "db", "info", ("kb_id", "doc_id", "count")
    ),
    "chunks deleted": EventSpec("chunks deleted", "db", "info", ("doc_id", "count")),
    "chunks read": EventSpec(
        "chunks read", "db", "info", ("doc_id", "kb_id", "page", "total", "count")
    ),
    "chunks read failed": EventSpec(
        "chunks read failed", "db", "warning", ("doc_id", "kb_id", "err")
    ),
    "collection deleted": EventSpec("collection deleted", "db", "info", ("name",)),
    "collection delete failed": EventSpec(
        "collection delete failed", "db", "warning", ("name",)
    ),
    "search stats": EventSpec(
        "search stats",
        "db",
        "info",
        ("kb_id", "collection_name", "collection_count", "top_k"),
    ),
    "search result": EventSpec(
        "search result", "db", "info", ("kb_id", "query_len", "result_count", "model")
    ),
    "search all done": EventSpec(
        "search all done", "db", "info", ("collections", "query_len", "result_count")
    ),
    "search collection failed": EventSpec(
        "search collection failed", "db", "warning", ("kb_id", "err")
    ),
    # [llm] Langfuse 追踪 / prompt 拉取 / LLM 内容深日志（3.4 批）
    "trace ready": EventSpec("trace ready", "llm", "info"),
    "trace init failed": EventSpec("trace init failed", "llm", "warning", ("err",)),
    "trace skip": EventSpec("trace skip", "llm", "warning", ("stage", "reason")),
    "prompt fetched": EventSpec("prompt fetched", "llm", "info", ("name", "version")),
    "prompt fetch failed": EventSpec(
        "prompt fetch failed", "llm", "warning", ("name", "err")
    ),
    "prompt fallback": EventSpec("prompt fallback", "llm", "info", ("name",)),
    "content start": EventSpec("content start", "llm", "info", ("model", "prompt")),
    "content end": EventSpec("content end", "llm", "info", ("model", "output")),
    # [cli] 离线工具（3.5 批：eval_ragas / eval_ragas_generate / rebuild_bm25 / check_retrieval / compare_rewrite）
    "testset loaded": EventSpec("testset loaded", "cli", "info", ("count",)),
    "evaluation run": EventSpec("evaluation run", "cli", "info", ("kb_id",)),
    "evaluation start": EventSpec("evaluation start", "cli", "info", ("samples",)),
    "evaluation done": EventSpec("evaluation done", "cli", "info"),
    "eval model init": EventSpec("eval model init", "cli", "info", ("sut", "judge")),
    "rag component init": EventSpec("rag component init", "cli", "info"),
    "vector store check": EventSpec("vector store check", "cli", "info", ("kb_id",)),
    "vector store empty": EventSpec("vector store empty", "cli", "error", ("kb_id",)),
    "ragas model missing": EventSpec("ragas model missing", "cli", "error"),
    "answers generation": EventSpec("answers generation", "cli", "info", ("count",)),
    "question answer start": EventSpec(
        "question answer start", "cli", "info", ("index", "query")
    ),
    "question answer done": EventSpec(
        "question answer done", "cli", "info", ("index", "answer_len", "contexts")
    ),
    "question answer failed": EventSpec(
        "question answer failed", "cli", "warning", ("index", "err")
    ),
    "metric mean": EventSpec("metric mean", "cli", "info", ("metric", "mean")),
    "metrics nan": EventSpec("metrics nan", "cli", "warning", ("counts",)),
    "results saved": EventSpec("results saved", "cli", "info", ("file",)),
    "report saved": EventSpec("report saved", "cli", "info", ("file",)),
    "eval report saved": EventSpec("eval report saved", "cli", "info", ("kb_id",)),
    "eval save failed": EventSpec("eval save failed", "cli", "warning", ("err",)),
    "vertexai stub created": EventSpec(
        "vertexai stub created", "cli", "info", ("file",)
    ),
    "question proofread failed": EventSpec(
        "question proofread failed", "cli", "warning", ("question", "err")
    ),
    "question proofread done": EventSpec(
        "question proofread done", "cli", "info", ("cleaned", "total")
    ),
    "kb meta failed": EventSpec("kb meta failed", "cli", "error", ("err",)),
    "chunk load start": EventSpec(
        "chunk load start", "cli", "info", ("kb_id", "whitelist")
    ),
    "chunks not found": EventSpec("chunks not found", "cli", "warning", ("doc_id",)),
    "no chunk data": EventSpec("no chunk data", "cli", "error"),
    "chunk load done": EventSpec("chunk load done", "cli", "info", ("docs", "chunks")),
    "generator init": EventSpec(
        "generator init", "cli", "info", ("model", "size", "chunks")
    ),
    "transforms setup": EventSpec("transforms setup", "cli", "info", ("steps",)),
    "knowledge graph found": EventSpec(
        "knowledge graph found", "cli", "info", ("file",)
    ),
    "knowledge graph build": EventSpec(
        "knowledge graph build", "cli", "info", ("chunks",)
    ),
    "knowledge graph saved": EventSpec(
        "knowledge graph saved", "cli", "info", ("file",)
    ),
    "testset generation start": EventSpec(
        "testset generation start", "cli", "info", ("size",)
    ),
    "testset saved": EventSpec(
        "testset saved", "cli", "info", ("file", "count", "version")
    ),
    "bm25 rebuild skip": EventSpec("bm25 rebuild skip", "cli", "info"),
    "kbs found": EventSpec("kbs found", "cli", "info", ("count",)),
    "kb chunks missing": EventSpec("kb chunks missing", "cli", "warning", ("kb_id",)),
    "bm25 rebuilt": EventSpec("bm25 rebuilt", "cli", "info", ("kb_id", "chunks")),
    "bm25 rebuild done": EventSpec(
        "bm25 rebuild done", "cli", "info", ("rebuilt", "skipped", "failed")
    ),
    "kb lookup": EventSpec("kb lookup", "cli", "info", ("name",)),
    "kb not found": EventSpec("kb not found", "cli", "error", ("name",)),
    "search start": EventSpec("search start", "cli", "info", ("query", "top_k")),
    "search failed": EventSpec("search failed", "cli", "warning", ("query", "err")),
    "llm invoke failed": EventSpec("llm invoke failed", "cli", "warning", ("err",)),
    "llm parse failed": EventSpec("llm parse failed", "cli", "warning", ("err", "raw")),
    "rerank error": EventSpec("rerank error", "cli", "warning", ("query", "err")),
    # [app] 应用边界（3.5 批：main.py 生命周期 + 全局异常兜底）
    "app starting": EventSpec("app starting", "app", "info"),
    "app stopping": EventSpec("app stopping", "app", "info"),
    "chroma warmup done": EventSpec(
        "chroma warmup done", "app", "info", ("collections",)
    ),
    "chroma warmup failed": EventSpec(
        "chroma warmup failed", "app", "warning", ("err",)
    ),
    "stale locks cleared": EventSpec("stale locks cleared", "app", "info", ("count",)),
    "locks clear failed": EventSpec("locks clear failed", "app", "warning", ("err",)),
    "biz error": EventSpec("biz error", "app", "error", ("code", "message")),
    "http error": EventSpec("http error", "app", "error", ("status", "detail")),
    "validation error": EventSpec("validation error", "app", "error", ("errors",)),
    "exception chain": EventSpec(
        "exception chain", "app", "error", ("depth", "type", "msg")
    ),
    "delegate skip": EventSpec(
        "delegate skip", "app", "warning", ("reason", "skills_dir")
    ),
    # [delegate] fork 子代理委派（delegate-hardening-observability）
    "delegate start": EventSpec(
        "delegate start",
        "delegate",
        "info",
        ("delegate_id", "skill", "thinking", "task_len"),
    ),
    "delegate model turn": EventSpec(
        "delegate model turn",
        "delegate",
        "info",
        (
            "delegate_id",
            "model",
            "usage_in",
            "usage_out",
            "usage_estimated",
            "latency_ms",
        ),
    ),
    "delegate end": EventSpec(
        "delegate end",
        "delegate",
        "info",
        ("delegate_id", "ok", "reason", "elapsed_ms", "result_len"),
    ),
    # [agent] 命令行直出跳过（skill_direct 节点兜底）
    "skill direct skip": EventSpec(
        "skill direct skip", "agent", "info", ("skill", "reason")
    ),
    # [agent] 直出轮确认门（design D18）
    "fork confirm asked": EventSpec(
        "fork confirm asked", "agent", "info", ("session_id",)
    ),
    "fork confirm unconfirmed": EventSpec(
        "fork confirm unconfirmed", "agent", "warning", ("session_id", "reason")
    ),
}
