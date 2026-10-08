"""WeComPresenter 单测的共享替身：假 sink 与事件流辅助，不发真实网络。

以普通模块（非 `test_` 前缀）承载，pytest 不收集；供「核心投影」「流式与降级」
「澄清呈现」三组测试 import 复用，避免替身定义重复。
"""

import asyncio
from collections.abc import AsyncIterator

from src.utils.sse import SSEEvent


class _RecordingSink:
    """记录每次 reply_stream 调用的假 sink。"""

    def __init__(self) -> None:
        """初始化，建立空调用记录。"""
        self.calls: list[tuple[str, bool, dict | None]] = []

    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        """记录一次流式回复调用。

        Args:
            content: 帧内容
            finish: 是否终态帧
            feedback: 首帧反馈标识（可为 None）
        """
        self.calls.append((content, finish, feedback))

    @property
    def contents(self) -> list[str]:
        """各帧内容列表（按发送顺序）。"""
        return [content for content, _finish, _fb in self.calls]


def _frozen_clock(value: float = 1000.0):
    """返回恒定时钟（配合非零节流间隔，可稳定断言"被跳过"）。

    Args:
        value: 恒定的时间值

    Returns:
        恒定时钟函数
    """

    def _now() -> float:
        return value

    return _now


async def _stream(events: list[SSEEvent]) -> AsyncIterator[SSEEvent]:
    """把事件列表变成异步迭代器（无延迟）。

    Args:
        events: 事件列表

    Yields:
        逐个事件
    """
    for event in events:
        yield event


async def _slow_stream(
    events: list[SSEEvent], delays: list[float]
) -> AsyncIterator[SSEEvent]:
    """按给定延迟逐个产出事件（用于保活与首帧超时用例）。

    Args:
        events: 事件列表
        delays: 与事件一一对应的延迟（秒）

    Yields:
        逐个事件
    """
    for event, delay in zip(events, delays):
        await asyncio.sleep(delay)
        yield event
