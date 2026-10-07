"""统一 token 计数入口 —— tiktoken 近似计数。

定位：跨调用点的**相对一致与量级**，不是绝对精度（非 OpenAI 模型无本地分词器；
真值仍以 provider 返回的 `usage_metadata` 为准）。

encoder 首次获取需联网下载词表（实测容器内约 4s、1.7MB，落 ~/.cache），故：
  - 进程内缓存 encoder，只付一次成本；
  - 应用启动时预热（`warm_token_encoder`），避免首个用户请求承担该延迟；
  - 拿不到 encoder 时降级为旧口径 `len(text) // 2` 并记一次 warning，绝不中断请求。
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any

import tiktoken
from langchain_core.messages import BaseMessage

from src.core import logging as core_logging
from src.core.log_events import Event

_ENCODING_NAME = "cl100k_base"

# 降级告警只记一次（encoder 缺失是环境级问题，逐次告警只会刷屏）
_WARNED_UNAVAILABLE = False


@lru_cache(maxsize=1)
def _encoder() -> Any:
    """返回进程内缓存的 tiktoken encoder。

    失败时向上抛异常：`lru_cache` **不缓存异常**，故调用方下次调用会重试，
    避免一次失败（如启动期网络未就绪）被永久固化为进程内的静默降级。
    """
    return tiktoken.get_encoding(_ENCODING_NAME)


def warm_token_encoder() -> bool:
    """预热 encoder（应用启动时调用），返回其可用性。

    Returns:
        True = encoder 可用；False = 不可用（调用方无需处理，计数会自行降级）
    """
    try:
        _encoder()
    except Exception:  # noqa: BLE001
        return False
    return True


def count_tokens(text: str) -> int:
    """返回文本的 token 数；encoder 不可用时降级为 `len(text) // 2`。"""
    global _WARNED_UNAVAILABLE
    if not text:
        return 0
    try:
        encoder = _encoder()
    except Exception as exc:  # noqa: BLE001
        if not _WARNED_UNAVAILABLE:
            _WARNED_UNAVAILABLE = True
            core_logging.log_event(Event.TOKEN_ENCODER_UNAVAILABLE, err=str(exc))
        return max(1, len(text) // 2)
    return len(encoder.encode(text))


def count_messages_tokens(messages: Sequence[Any]) -> int:
    """返回消息列表的 token 数：各条 str 型 content 以空格连接后统一计数。

    非 `BaseMessage` 元素与 list 型（多模态）content 一律跳过。

    Args:
        messages: 待计数的消息序列

    Returns:
        连接后文本的 token 数；无可计数内容时为 0
    """
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, BaseMessage):
            continue
        if isinstance(message.content, str):
            parts.append(message.content)
    return count_tokens(" ".join(parts))
