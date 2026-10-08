"""企微流式投影层：把结构化 SSEEvent 流投影成企微流式帧（design D4）。

三段式（start/update/finalize）对标 openakita StreamPresenter：累积快照与共享
节流收敛在本层；业务侧只喂事件，不关心通道渲染差异。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable

from loguru import logger

from src.channels.base import ReplySink
from src.config.wecom_presenter import (
    FEEDBACK_ID_ENABLED,
    FIRST_FRAME_TIMEOUT_SECONDS,
    KEEPALIVE_INTERVAL_SECONDS,
    MAX_INTERMEDIATE_FRAMES,
    MAX_STREAM_CHARS,
    MIN_SEND_INTERVAL_SECONDS,
    TRACE_FOOTER_EFFECTIVE,
    WeComPresenterTexts,
)
from src.utils.sse import (
    SSEAbstentionEvent,
    SSEAgentUsedEvent,
    SSECitationEvent,
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

# 队列哨兵：上游事件流已结束（不再有事件入队）
_EVENTS_END: object = object()


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
        keepalive_seconds: float = KEEPALIVE_INTERVAL_SECONDS,
        first_frame_timeout: float = FIRST_FRAME_TIMEOUT_SECONDS,
        max_frames: int = MAX_INTERMEDIATE_FRAMES,
        max_chars: int = MAX_STREAM_CHARS,
        monotonic: Callable[[], float] = time.monotonic,
        footer_enabled: bool = TRACE_FOOTER_EFFECTIVE,
        feedback_enabled: bool = FEEDBACK_ID_ENABLED,
    ) -> None:
        """初始化。

        Args:
            sink: 回复出口
            trace_id: 本轮 trace_id（终态 footer 与首帧反馈标识用）
            min_interval_seconds: 发送最小间隔（节流下限）
            keepalive_seconds: 无事件时的保活间隔；须小于「首帧起 6 分钟」的收尾时限
            first_frame_timeout: 首帧最迟等待；超时先发占位帧，不空等到收尾时限
            max_frames: 承载正文的中间帧数上限（占位帧与保活帧不计入）
            max_chars: 单流累计正文长度上限（超出保留尾部）
            monotonic: 单调时钟（测试注入以稳定断言节流）
            footer_enabled: 终态是否附 trace_id footer
            feedback_enabled: 首帧是否携带反馈标识
        """
        self._sink = sink
        self._trace_id = trace_id
        self._min_interval_seconds = min_interval_seconds
        self._keepalive_seconds = keepalive_seconds
        self._first_frame_timeout = first_frame_timeout
        self._max_frames = max_frames
        self._max_chars = max_chars
        self._monotonic = monotonic
        self._footer_enabled = footer_enabled
        self._feedback_enabled = feedback_enabled

        self._text: str = ""
        self._placeholder: str = WeComPresenterTexts.PLACEHOLDER_TEXT
        self._final: bool = False
        self._last_sent: str | None = None
        self._last_sent_at: float = float("-inf")
        self._frames_sent: int = 0
        self._sources: list[SSECitationEvent] = []
        self._abstained: bool = False
        self._error: str | None = None
        self._degraded: bool = False
        self._feedback_sent: bool = False

    async def run(self, events: AsyncIterator[SSEEvent]) -> None:
        """消费上游事件流并完成一轮投影。

        事件流以队列暴露、对「取下一个事件」施加超时：直接对上游生成器的
        在途取值施超时取消会终结生成器（`_subscribe_events` 不捕
        `CancelledError`），恰在需要保活时把长流静默截断。

        Args:
            events: 结构化事件流（如 `TurnHandle.events`）
        """
        queue: asyncio.Queue = asyncio.Queue()
        pump = asyncio.create_task(self._pump(events, queue))
        try:
            await self._consume(queue)
        except Exception:
            # 消费循环意外抛出（不是 error 事件路径）时仍须收尾：投影 spec
            # 禁止留下未结束的悬挂流；先补发终态帧，再把异常交给调用方
            await self.finalize()
            raise
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)

    async def _pump(
        self, events: AsyncIterator[SSEEvent], queue: asyncio.Queue
    ) -> None:
        """后台泵：把上游事件入队；上游结束或出错时补哨兵。

        Args:
            events: 上游结构化事件流
            queue: 投影层消费的队列
        """
        try:
            async for event in events:
                queue.put_nowait(event)
        except Exception as e:  # noqa: BLE001
            logger.error("[wecom] presenter pump aborted err={}", e)
        finally:
            queue.put_nowait(_EVENTS_END)

    async def _consume(self, queue: asyncio.Queue) -> None:
        """消费循环：超时发保活帧（不取消上游），终态或哨兵后收尾。

        超时判据是「**是否已发出首帧**」（`_last_sent is None`），而不是「是否
        收到过事件」：丢弃类事件（思考过程 / 模型信息 / 子代理过程 / 任务看板 /
        会话绑定智能体）不产生任何帧，若按"收到事件"就切到保活间隔，占位首帧
        会从 `first_frame_timeout` 掉到 `keepalive_seconds`（默认 240s）——违反
        「若在合理时限内无任何可渲染内容，SHALL 先发送占位内容，不得空等到超时」。

        Args:
            queue: 事件队列
        """
        while True:
            if self._last_sent is None:
                timeout = self._first_frame_timeout
            else:
                timeout = self._keepalive_seconds
            try:
                item = await asyncio.wait_for(queue.get(), timeout)
            except TimeoutError:
                await self._flush(keepalive=True)
                continue
            if item is _EVENTS_END:
                break
            await self.update(item)
            if isinstance(item, (SSEDoneEvent, SSEErrorEvent)):
                break
        await self.finalize()

    async def update(self, event: SSEEvent) -> None:
        """投影单个事件：累积正文 / 收集引用 / 记录终态标记，必要时发送一帧。

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
        elif isinstance(event, SSECitationEvent):
            self._sources.append(event)
        elif isinstance(event, SSEAbstentionEvent):
            self._abstained = True
        elif isinstance(event, SSEErrorEvent):
            # 脱敏：原始异常只进日志，不得外泄给企微用户
            logger.error("[wecom] presenter got error event err={}", event.error)
            self._error = WeComPresenterTexts.ERROR_TEXT
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
        if isinstance(event, SSEDoneEvent):
            # done 意味着本轮已收尾，这里不再发中间帧，统一由 finalize 发终态帧
            return
        await self._flush()

    async def finalize(self) -> None:
        """发送终态帧（含引用/兜底/footer）；流式失败时退化为一次性收尾。"""
        if self._final:
            return
        self._final = True
        content = self._render_final()
        if self._degraded:
            await self._send_raw(content, finish=True)
            return
        sent = await self._send(content, finish=True)
        if not sent:
            await self._send_raw(content, finish=True)

    def _render_final(self) -> str:
        """终态正文：正文 + 脱敏错误 + 转人工提示 + 参考来源 + trace_id footer。"""
        parts: list[str] = []
        body = self._render_answer()
        if not is_blank(body):
            parts.append(body)
        if self._error is not None:
            parts.append(self._error)
        if self._abstained:
            parts.append(WeComPresenterTexts.ABSTENTION_TEXT)
        if not parts:
            parts.append(WeComPresenterTexts.FALLBACK_TEXT)
        sources = self._render_sources()
        if sources:
            parts.append(sources)
        if self._footer_enabled:
            parts.append(
                WeComPresenterTexts.TRACE_FOOTER_TEMPLATE.format(self._trace_id)
            )
        return "\n\n".join(parts)

    def _render_sources(self) -> str:
        """文末「参考来源」段；无引用返回空串。"""
        if not self._sources:
            return ""
        lines: list[str] = [WeComPresenterTexts.SOURCES_TITLE]
        for item in self._sources:
            lines.append(f"- {item.source}（第 {item.page} 页）")
        return "\n".join(lines)

    def _feedback_arg(self) -> dict | None:
        """首帧的反馈标识；未开启或已发过则返回 None。"""
        if not self._feedback_enabled:
            return None
        if self._feedback_sent:
            return None
        return {"id": self._trace_id}

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

    async def _flush(self, *, keepalive: bool = False) -> None:
        """按节流与上限规则决定是否发送当前快照。

        保活帧（`keepalive=True`）豁免「内容未变跳过」「空白不发送」「帧数上限」
        与节流：长静默期累积正文与上一帧相同，不豁免则保活无从发出；保活即
        重发当前快照，不新造可见文案。

        Args:
            keepalive: 是否保活帧
        """
        if self._final or self._degraded:
            return
        carries_answer = self._has_answer()
        content = self._current_content()
        if not keepalive:
            if content == self._last_sent:
                return
            if carries_answer and self._frames_sent >= self._max_frames:
                return
            if self._monotonic() - self._last_sent_at < self._min_interval_seconds:
                return
        sent = await self._send(content, finish=False)
        if sent and carries_answer and not keepalive:
            self._frames_sent += 1

    async def _send(self, content: str, finish: bool) -> bool:
        """发送一帧；失败时置降级标志并返回 False（不抛异常）。

        Args:
            content: 本帧内容
            finish: 是否为终态帧

        Returns:
            True 表示发送成功
        """
        try:
            await self._sink.reply_stream(
                content, finish, feedback=self._feedback_arg()
            )
        except Exception as e:  # noqa: BLE001
            logger.error("[wecom] presenter reply failed finish={} err={}", finish, e)
            self._degraded = True
            return False
        self._feedback_sent = True
        self._last_sent = content
        self._last_sent_at = self._monotonic()
        return True

    async def _send_raw(self, content: str, finish: bool) -> None:
        """降级收尾：单次发送最终内容，失败只记日志。"""
        try:
            await self._sink.reply_stream(
                content, finish, feedback=self._feedback_arg()
            )
        except Exception as e:  # noqa: BLE001
            logger.error("[wecom] presenter fallback reply failed err={}", e)
