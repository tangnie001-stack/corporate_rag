"""统一日志配置 — 集中管理 Loguru sinks 和第三方库日志收编。

全部日志写入文件（INFO 级别按天轮转保留 7 天，ERROR 级别保留 30 天）。
提供 InterceptHandler 将标准库 logging 路由至 Loguru。
"""

import json
import logging
import os
import re

from loguru import logger

from src.core.log_events import EVENT_SPECS, SIGNAL_PREFIX, Event, Signal


class InterceptHandler(logging.Handler):
    """将标准库 logging 无缝路由到 Loguru。

    用于收编 uvicorn、fastapi 等三方库的日志，
    确保所有日志通过 Loguru 统一管道输出。
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


_LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {extra[trace_id]:36} | {extra[session_id]:36} | {name}:{function}:{line} - {message}"
_LOG_DIR = os.getenv("LOG_DIR", "logs")

# ==== 数据链路追踪日志常量 ====
LOG_MAX_BODY = 10 * 1024 * 1024  # 单条日志最大 10MB

# SQL 方法层 — 跳过全量返回值记录（只记 count + 关键参数）
SQL_SKIP_FULL_LOG = {"get_messages"}

# API 路由层 — 跳过全量响应体记录（只记 path + status_code）
API_SKIP_FULL_LOG = {"/api/sessions/messages"}


def log_sql_result(method: str, sql, rows, **extra) -> None:
    """统一 SQL 返回值日志。

    方法名在 SQL_SKIP_FULL_LOG 中时只记录 count + 额外参数，
    否则记录完整 data。超过 LOG_MAX_BODY 时截断。
    """
    count = (
        len(rows) if isinstance(rows, (list, dict)) else (1 if rows is not None else 0)
    )
    if method in SQL_SKIP_FULL_LOG:
        extra_str = " | ".join(f"{k}={v}" for k, v in extra.items())
        logger.info(
            "[SQL] method={} | sql={} | rows={} | {}", method, sql, count, extra_str
        )
    else:
        data_str = str(rows)
        if len(data_str) > LOG_MAX_BODY:
            data_str = (
                data_str[:LOG_MAX_BODY]
                + f"... (truncated, total={len(data_str)} chars)"
            )
        try:
            logger.info(
                "[SQL] method={} | sql={}| rows={} | data={}",
                method,
                sql,
                count,
                data_str,
            )
        except Exception:  # noqa: BLE001
            logger.info(
                "[SQL] method={} | sql={}| rows={} | data=<serialization_error>",
                method,
                sql,
                count,
            )


def _setup_trace_id_patcher() -> None:
    """配置 Loguru patcher，自动注入当前请求的 trace_id / session_id。

    从 trace_context 模块的 ContextVar 中读取当前 trace_id 与 session_id，
    写入每一条日志记录的 extra 字段。
    如果 ContextVar 为空（CLI 模式），自动生成一个 trace_id。
    """
    from src.infra.llm.trace_context import current_trace_id as _trace_var

    # CLI 模式：没有外部传入的 trace_id 时自动生成
    if not _trace_var.get():
        from src.infra.llm.tracing import new_trace_id

        _trace_var.set(new_trace_id())

    def _patcher(record):
        from src.infra.llm.trace_context import (
            current_session_id as _session_var,
        )
        from src.infra.llm.trace_context import (
            current_trace_id as _trace_var,
        )

        record["extra"]["trace_id"] = _trace_var.get() or ""
        record["extra"]["session_id"] = _session_var.get() or ""

    logger.configure(extra={"trace_id": "", "session_id": ""}, patcher=_patcher)


def setup_logging(configure_trace_id: bool = False) -> None:
    """初始化 Loguru 日志配置。

    Args:
        configure_trace_id: 是否注入 trace_id patcher（仅 API 模式需启用）

    日志文件：
      - app_{date}.log — INFO 级别，按天轮转，保留 7 天，异步写入
      - error_{date}.log — ERROR 级别，按天轮转，保留 30 天，异步写入
    """
    os.makedirs(_LOG_DIR, exist_ok=True)

    # 移除默认 sink，防止重复
    logger.remove()

    # 确保 extra 字典至少包含 trace_id / session_id 键（即使未配置 patcher）
    logger.configure(extra={"trace_id": "", "session_id": ""})

    logger.add(
        f"{_LOG_DIR}/app_{{time:YYYY-MM-DD}}.log",
        format=_LOG_FORMAT,
        rotation="1 day",
        retention="7 days",
        level="INFO",
        encoding="utf-8",
        enqueue=True,
    )
    logger.add(
        f"{_LOG_DIR}/error_{{time:YYYY-MM-DD}}.log",
        format=_LOG_FORMAT,
        rotation="1 day",
        retention="30 days",
        level="ERROR",
        encoding="utf-8",
        enqueue=True,
    )

    # 收编标准库日志到 Loguru
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        _log = logging.getLogger(name)
        _log.handlers = [InterceptHandler()]
        _log.propagate = False

    # trace_id patcher（仅 API 模式）
    if configure_trace_id:
        _setup_trace_id_patcher()


# ==== 统一事件日志 helper（注册表驱动） ====

# token 安全字符集：命中则裸写，否则引号 + JSON 转义（logging-rules.md）
_TOKEN_SAFE = re.compile(r"^[A-Za-z0-9_./:@-]+$")


def encode_value(value: object) -> str:
    """按值类型编码日志字段文本（helper 唯一实现，`需要才引`）。

    int/bool 裸写；token 安全字符串裸写；其余字符串 `json.dumps`
    （引号 + JSON 转义）；数组/容器紧凑 JSON（无空格）。query 等长文本
    由调用方完整传入，本函数不截断。
    """
    if isinstance(value, bool):
        if value:
            return "true"
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if _TOKEN_SAFE.fullmatch(value):
            return value
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def log_event(event: Event, **fields: object) -> None:
    """按注册表驱动输出分层前缀英文 k=v 事件日志。

    事件 key = Event 枚举成员（log_events import 校验保证必然已登记），
    前缀/事件词/级别取 EVENT_SPECS，调用点不传前缀与级别。

    首行 Event(event) 收口：裸字符串合法值规范化为枚举成员；非法值抛
    ValueError（堵住绕过枚举的自由文本路径，无任何兜底落盘）。

    Args:
        event: 事件枚举成员（值 = 事件名）
        fields: k=v 字段，经 encode_value 值类型编码
    """
    event = Event(event)  # 收口：裸字符串合法值规范化，非法值抛 ValueError
    spec = EVENT_SPECS[event.value]
    parts = [f"{k}={encode_value(v)}" for k, v in fields.items()]
    message = f"[{spec.prefix}] {spec.name}"
    if parts:
        message += " " + " ".join(parts)
    # spec.level 为小写逻辑级别，Loguru logger.log 须用大写级别名
    logger.log(spec.level.upper(), message)


def retrieval_signal(
    signal: Signal, query: str, iteration: int, **fields: object
) -> None:
    """输出检索行为信号日志（P1 Change 2 保留前缀，info 级）。

    Args:
        signal: Signal 枚举成员（reretrieve/to_web/...）
        query: 用户查询文本（完整记录，不截断，JSON 转义保持行可解析）
        iteration: agent 迭代序号
        fields: 附加字段（kb_id/result_count/reason 等），与 log_event 同走值编码
    """
    parts = [f"{k}={encode_value(v)}" for k, v in fields.items()]
    message = (
        f"{SIGNAL_PREFIX} signal={signal.value} "
        f"query={json.dumps(query, ensure_ascii=False)} iteration={iteration}"
    )
    if parts:
        message += " " + " ".join(parts)
    logger.info(message)
