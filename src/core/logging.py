"""统一日志配置 — 集中管理 Loguru sinks 和第三方库日志收编。

支持 API 模式（写文件 + 控制台）和 CLI 模式（仅控制台）。
提供 InterceptHandler 将标准库 logging 路由至 Loguru。
"""

import logging
import os

from loguru import logger


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


_LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {extra[trace_id]:36} | {name}:{function}:{line} - {message}"
_LOG_DIR = os.getenv("LOG_DIR", "logs")

# ==== 数据链路追踪日志常量 ====
LOG_MAX_BODY = 10 * 1024 * 1024  # 单条日志最大 10MB

# SQL 方法层 — 跳过全量返回值记录（只记 count + 关键参数）
SQL_SKIP_FULL_LOG = {"get_messages"}

# API 路由层 — 跳过全量响应体记录（只记 path + status_code）
API_SKIP_FULL_LOG = {"/api/sessions/messages"}


def _setup_trace_id_patcher() -> None:
    """配置 Loguru patcher，自动注入当前请求的 trace_id。

    从 trace_context 模块的 ContextVar 中读取当前 trace_id，
    写入每一条日志记录的 extra 字段。
    仅在 API 进程（有 HTTP 请求上下文）中启用。
    """
    from src.infra.llm.trace_context import current_trace_id as _trace_var

    def _patcher(record):
        record["extra"]["trace_id"] = _trace_var.get() or ""

    logger.configure(extra={"trace_id": ""}, patcher=_patcher)


def setup_logging(write_to_file: bool = True, configure_trace_id: bool = False) -> None:
    """初始化 Loguru 日志配置。

    Args:
        write_to_file: 是否写入文件（API 模式 True，CLI 模式 False）
        configure_trace_id: 是否注入 trace_id patcher（API 模式 True，CLI 模式 False）

    API 模式配置：
      - app_{date}.log — INFO 级别，按天轮转，保留 7 天，异步写入
      - error_{date}.log — ERROR 级别，按天轮转，保留 30 天，异步写入

    CLI 模式配置：
      - stderr — INFO 级别，彩色输出（不写文件）
    """
    # 确保日志目录存在
    os.makedirs(_LOG_DIR, exist_ok=True)

    # 移除默认 sink，防止重复
    logger.remove()

    # 文件 sink（仅 API 模式）
    if write_to_file:
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
