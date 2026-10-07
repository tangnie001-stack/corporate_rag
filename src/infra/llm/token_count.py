"""统一 token 计数入口 —— tiktoken 近似计数。

定位：跨调用点的**相对一致与量级**，不是绝对精度（非 OpenAI 模型无本地分词器；
真值仍以 provider 返回的 `usage_metadata` 为准）。

encoder 首次获取需联网下载词表（实测容器内约 4s、1.7MB，落 ~/.cache），故：
  - encoder 成功获取后进程内缓存，正常运行只付一次成本；获取失败不缓存成功结果，
    但按冷却窗口负缓存（见 `_get_encoder`），避免逐调用重试把网络超时放大到每一次
    模型调用上；
  - 应用启动时预热（`warm_token_encoder`），避免首个用户请求承担该延迟；
  - 拿不到 encoder 时降级为旧口径 `len(text) // 2` 并记一次 warning，绝不中断请求。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

import tiktoken
from langchain_core.messages import BaseMessage

from src.core import logging as core_logging
from src.core.log_events import Event

_ENCODING_NAME = "cl100k_base"

# 降级告警只记一次（encoder 缺失是环境级问题，逐次告警只会刷屏；跨冷却窗口也只一次）
_WARNED_UNAVAILABLE = False

# 失败后的重试冷却窗口（秒）：encoder 不可用是环境级问题，逐调用重试会把网络超时
# 放大到每一次模型调用上；冷却窗口到期后允许再试一次（保留可恢复语义）。
_ENCODER_RETRY_COOLDOWN_S = 60.0

# 上次失败时刻（monotonic 秒）；None = 从未失败
_encoder_failed_at: float | None = None


@lru_cache(maxsize=1)
def _encoder() -> Any:
    """返回进程内缓存的 tiktoken encoder。

    失败时向上抛异常：`lru_cache` **不缓存异常**，故下次调用会重试，避免一次失败
    （如启动期网络未就绪）被永久固化为进程内的静默降级。重试频率由 `_get_encoder`
    的冷却窗口约束。
    """
    return tiktoken.get_encoding(_ENCODING_NAME)


def _get_encoder() -> Any | None:
    """取 encoder；失败时按冷却窗口负缓存，返回 None 表示不可用。

    冷却窗口内不再重试（直接返回 None 走降级），窗口到期后允许再试一次，使
    "网络恢复后自动恢复精确计数"这一可恢复语义得以保留。告警仍只在首次失败时
    记一次（`_WARNED_UNAVAILABLE`），跨冷却窗口不重复。
    """
    global _encoder_failed_at, _WARNED_UNAVAILABLE
    now = time.monotonic()
    # 嵌套 if 是刻意的：冷却判定分两步表达（先问"失败过没有"、再问"还在窗口内吗"），
    # 比合并成一个 and 更贴合语义，且避免退化成三元写法；故抑制 SIM102
    if _encoder_failed_at is not None:  # noqa: SIM102
        if now - _encoder_failed_at < _ENCODER_RETRY_COOLDOWN_S:
            return None
    try:
        return _encoder()
    except Exception as exc:  # noqa: BLE001
        _encoder_failed_at = now
        if not _WARNED_UNAVAILABLE:
            _WARNED_UNAVAILABLE = True
            core_logging.log_event(Event.TOKEN_ENCODER_UNAVAILABLE, err=str(exc))
        return None


def warm_token_encoder() -> bool:
    """预热 encoder（应用启动时调用），返回其可用性。

    Returns:
        True = encoder 可用；False = 不可用（调用方无需处理，计数会自行降级）
    """
    return _get_encoder() is not None


def count_tokens(text: str) -> int:
    """返回文本的 token 数；encoder 不可用时降级为 `len(text) // 2`。"""
    if not text:
        return 0
    encoder = _get_encoder()
    if encoder is None:
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
