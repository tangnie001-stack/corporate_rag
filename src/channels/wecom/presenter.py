"""企微流式投影层：把结构化 SSEEvent 流投影成企微流式帧（design D4）。

三段式（start/update/finalize）对标 openakita StreamPresenter：累积快照与共享
节流收敛在本层；业务侧只喂事件，不关心通道渲染差异。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable

from src.channels.base import ReplySink
from src.config.wecom_presenter import (
    MAX_INTERMEDIATE_FRAMES,
    MAX_STREAM_CHARS,
    MIN_SEND_INTERVAL_SECONDS,
    WeComPresenterTexts,
)
from src.utils.sse import (
    SSEAgentUsedEvent,
    SSEDelegateEvent,
    SSEDoneEvent,
    SSEErrorEvent,
    SSEEvent,
    SSEModelInfoEvent,
    SSEReasoningDeltaEvent,
    SSEStatusEvent,
    SSETaskEvent,
    SSETokenEvent,
)

# 零宽字符：仅含这些字符的内容视为空白（不得产生空气泡）
_ZERO_WIDTH_CHARS: tuple[str, ...] = ("\u200b", "\u200c", "\u200d", "\ufeff")


def is_blank(text: str) -> bool:
    """判断文本是否为空白或仅含零宽字符。

    Args:
        text: 待判断文本

    Returns:
        True 表示去掉空白与零宽字符后为空
    """
    stripped = text
    for ch in _ZERO_WIDTH_CHARS:
        stripped = stripped.replace(ch, "")
    return not stripped.strip()


class WeComPresenter:
    """把 SSEEvent 流投影成企微流式帧（每帧发累积全文）。"""

    def __init__(
        self,
        sink: ReplySink,
        trace_id: str,
        *,
        min_interval_seconds: float = MIN_SEND_INTERVAL_SECONDS,
        max_frames: int = MAX_INTERMEDIATE_FRAMES,
        max_chars: int = MAX_STREAM_CHARS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化。

        Args:
            sink: 回复出口
            trace_id: 本轮 trace_id（终态 footer 与首帧反馈标识用）
            min_interval_seconds: 发送最小间隔（节流下限）
            max_frames: 承载正文的中间帧数上限（占位帧不计入）
            max_chars: 单流累计正文长度上限（超出保留尾部）
            monotonic: 单调时钟（测试注入以稳定断言节流）
        """
        self._sink = sink
        self._trace_id = trace_id
        self._min_interval_seconds = min_interval_seconds
        self._max_frames = max_frames
        self._max_chars = max_chars
        self._monotonic = monotonic

        self._text: str = ""
        self._placeholder: str = WeComPresenterTexts.PLACEHOLDER_TEXT
        self._final: bool = False
        self._last_sent: str | None = None
        self._last_sent_at: float = float("-inf")
        self._frames_sent: int = 0

    async def run(self, events: AsyncIterator[SSEEvent]) -> None:
        """消费上游事件流并完成一轮投影。

        终态由 `done`/`error` 事件或事件流自然结束触发。

        Args:
            events: 结构化事件流（如 `TurnHandle.events`）
        """
        async for event in events:
            await self.update(event)
            if isinstance(event, (SSEDoneEvent, SSEErrorEvent)):
                break
        await self.finalize()

    async def update(self, event: SSEEvent) -> None:
        """投影单个事件：累积正文 / 记录占位；无渲染通道的事件丢弃。

        丢弃类事件（思考过程 / 子代理过程 / 任务看板 / 模型信息 / 会话绑定
        智能体）在企微无对应渲染，丢弃且不得中断本轮流。

        Args:
            event: 单个结构化事件
        """
        if isinstance(event, SSETokenEvent):
            self._text += event.token
        elif isinstance(event, SSEStatusEvent):
            if is_blank(self._text):
                self._placeholder = event.message
        elif isinstance(
            event,
            (
                SSEReasoningDeltaEvent,
                SSEDelegateEvent,
                SSETaskEvent,
                SSEModelInfoEvent,
                SSEAgentUsedEvent,
            ),
        ):
            return
        await self._flush()

    async def finalize(self) -> None:
        """发送终态帧（幂等）。"""
        if self._final:
            return
        self._final = True
        await self._send(self._current_content(), finish=True)

    def _render_answer(self) -> str:
        """当前累积正文；超过长度上限时保留尾部。"""
        if len(self._text) > self._max_chars:
            return self._text[-self._max_chars :]
        return self._text

    def _has_answer(self) -> bool:
        """累积正文是否非空白。"""
        return not is_blank(self._render_answer())

    def _current_content(self) -> str:
        """当前应发送的内容：有正文用正文，否则用占位文案。"""
        if self._has_answer():
            return self._render_answer()
        return self._placeholder

    async def _flush(self) -> None:
        """按节流与上限规则决定是否发送当前快照。"""
        if self._final:
            return
        carries_answer = self._has_answer()
        content = self._current_content()
        if content == self._last_sent:
            return
        if carries_answer and self._frames_sent >= self._max_frames:
            return
        if self._monotonic() - self._last_sent_at < self._min_interval_seconds:
            return
        await self._send(content, finish=False)
        if carries_answer:
            self._frames_sent += 1

    async def _send(self, content: str, finish: bool) -> None:
        """发送一帧并记录发送内容与时刻。

        Args:
            content: 本帧内容
            finish: 是否为终态帧
        """
        await self._sink.reply_stream(content, finish)
        self._last_sent = content
        self._last_sent_at = self._monotonic()
