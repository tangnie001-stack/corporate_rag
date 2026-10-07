"""统一 token 计数入口 —— tiktoken 近似计数。

定位：跨调用点的**相对一致与量级**，不是绝对精度（非 OpenAI 模型无本地分词器；
真值仍以 provider 返回的 `usage_metadata` 为准）。

encoder 首次获取需联网下载词表（实测容器内约 4s、1.7MB，落 ~/.cache），故：
  - 进程内缓存 encoder，只付一次成本；
  - 应用启动时预热（`warm_token_encoder`），避免首个用户请求承担该延迟；
  - 拿不到 encoder 时降级为旧口径 `len(text) // 2` 并记一次 warning，绝不中断请求。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

import tiktoken
from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)

_ENCODING_NAME = "cl100k_base"

# 降级告警只记一次（encoder 缺失是环境级问题，逐次告警只会刷屏）
_WARNED_UNAVAILABLE = False


@lru_cache(maxsize=1)
def _encoder() -> Any | None:
    """返回进程内缓存的 tiktoken encoder；不可用时返回 None（并记一次 warning）。"""
    global _WARNED_UNAVAILABLE
    try:
        return tiktoken.get_encoding(_ENCODING_NAME)
    except Exception as exc:  # noqa: BLE001
        if not _WARNED_UNAVAILABLE:
            _WARNED_UNAVAILABLE = True
            logger.warning(
                "[llm] token encoder unavailable, fallback to len//2 err=%s", exc
            )
        return None


def warm_token_encoder() -> bool:
    """预热 encoder（应用启动时调用），返回其可用性。

    Returns:
        True = encoder 可用；False = 不可用（调用方无需处理，计数会自行降级）
    """
    return _encoder() is not None


def count_tokens(text: str) -> int:
    """返回文本的 token 数；encoder 不可用时降级为 `len(text) // 2`。"""
    if not text:
        return 0
    encoder = _encoder()
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
