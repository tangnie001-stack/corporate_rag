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
