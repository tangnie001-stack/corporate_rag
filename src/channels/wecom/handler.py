"""企微入站 → 站点同款 Agent 管线的桥接 handler（design D1/D9/D12/D14/D19/D21）。

本层只做五件事：标识推导 → msgid 去重 → trace_id 三路 → 事件分流 → 调 `start_turn`
并把事件流交给投影层。单轮生成的编排（落库前置 / 原子闸门 / 失败语义 / 收尾）在
`services.turn_runner`（D12），呈现细节在 `channels/wecom/presenter.py`（D4）。

依赖方向：本模块可 import `services.{app_service,turn_runner}` 这类叶子模块，但
**不得** import `src.services.wecom_service`（后者要 import 本模块，会成环）。
仅用于长连接路径（回调 sink 只保留最后一次回包，跑不了几十秒的回合，D15）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

from loguru import logger

from src.channels.base import InboundMessage, ReplySink
from src.channels.wecom.bounded_map import BoundedTtlMap
from src.channels.wecom.presenter import WeComPresenter
from src.channels.wecom.session import (
    build_session_title,
    derive_session_id,
    derive_user_id,
)
from src.config import wecom_channel
from src.config.const import WECOM_EVENT_FEEDBACK
from src.config.wecom_presenter import WeComPresenterTexts
from src.core.logging import encode_value
from src.infra.llm.trace_context import current_trace_id
from src.infra.llm.tracing import new_trace_id
from src.services import turn_runner
from src.services.app_service import AppService
from src.utils.sse import SSEAskUserEvent, SSEEvent

# 单轮生成入口签名（生产传 `services.turn_runner.start_turn`；测试注入替身）
StartTurnCallable = Callable[..., Awaitable[turn_runner.TurnHandle]]
GetServiceCallable = Callable[[], Awaitable[AppService]]


def extract_feedback_id(raw: dict) -> str | None:
    """从反馈回执的原始帧取出承载值（即我们带出去的 trace_id）。

    字段路径以 Spike E10 实测帧为准：`body.event.feedback_event.id`
    （见 `docs/agents/wecom-sdk-facts.md`）。

    Args:
        raw: 入站原始明文（`InboundMessage.raw`）

    Returns:
        trace_id 字符串；结构缺失或无 id 时返回 None
    """
    event = raw.get("event")
    if not isinstance(event, dict):
        return None
    payload = event.get("feedback_event")
    if not isinstance(payload, dict):
        return None
    feedback_id = payload.get("id")
    if not isinstance(feedback_id, str) or not feedback_id:
        return None
    return feedback_id


class RagChannelHandler:
    """把企微入站消息喂进站点同款 Agent 管线，并把事件流投影成企微回复。"""

    def __init__(
        self,
        *,
        start_turn: StartTurnCallable,
        get_service: GetServiceCallable,
        resolve_bot_key: Callable[[str], str | None],
        dedup: BoundedTtlMap,
        triggers: BoundedTtlMap,
        kb_id: str = "",
    ) -> None:
        """初始化。

        Args:
            start_turn: 单轮生成入口（生产传 `services.turn_runner.start_turn`）
            get_service: AppService 取用器（生产传 `services.app_service.get_app_service`）
            resolve_bot_key: aibotid → bot_key（生产传 `wecom_service._resolve_bot_key`）
            dedup: msgid 去重表（有界）
            triggers: 澄清"会话 → 触发者"登记表（有界）
            kb_id: 本轮默认知识库（空串 = 不检索，与站点逻辑一致）
        """
        self._start_turn = start_turn
        self._get_service = get_service
        self._resolve_bot_key = resolve_bot_key
        self._dedup = dedup
        self._triggers = triggers
        self._kb_id = kb_id

    @classmethod
    def build_default(
        cls,
        *,
        start_turn: StartTurnCallable,
        get_service: GetServiceCallable,
        resolve_bot_key: Callable[[str], str | None],
    ) -> RagChannelHandler:
        """按配置装配默认实例（去重/触发者映射取 `wecom_channel` 的窗口与容量）。

        Args:
            start_turn: 单轮生成入口
            get_service: AppService 取用器
            resolve_bot_key: aibotid → bot_key

        Returns:
            配置齐备的 RagChannelHandler
        """
        return cls(
            start_turn=start_turn,
            get_service=get_service,
            resolve_bot_key=resolve_bot_key,
            dedup=BoundedTtlMap(
                capacity=wecom_channel.DEDUP_CAPACITY,
                ttl_seconds=wecom_channel.DEDUP_TTL_SECONDS,
            ),
            triggers=BoundedTtlMap(
                capacity=wecom_channel.TRIGGER_MAP_CAPACITY,
                ttl_seconds=wecom_channel.TRIGGER_MAP_TTL_SECONDS,
            ),
        )

    def registered_trigger(self, session_id: str) -> str | None:
        """读澄清触发者登记（供组 6 回填校验来源；当前仅登记）。"""
        return self._triggers.get(session_id)

    async def __call__(self, msg: InboundMessage, sink: ReplySink) -> None:
        """处理一条入站消息（`MessageHandler` 协议）。

        Args:
            msg: 统一入站事件
            sink: 回复出口
        """
        bot_key = self._resolve_bot_key(msg.aibotid)
        if bot_key is None:
            logger.warning("[wecom] unknown bot aibotid={}", encode_value(msg.aibotid))
            return
        if not self._dedup.mark_if_new(msg.msgid, ""):
            logger.info(
                "[wecom] duplicate msgid dropped bot_key={} msgid={}",
                encode_value(bot_key),
                msg.msgid,
            )
            return
        if msg.msgtype == "event":
            self._handle_event(bot_key, msg)
            return
        if not msg.text:
            logger.info(
                "[wecom] unsupported msgtype bot_key={} msgtype={}",
                encode_value(bot_key),
                msg.msgtype,
            )
            await sink.reply_stream(
                wecom_channel.WeComChannelTexts.UNSUPPORTED_TEXT, finish=True
            )
            return
        await self._run_turn(bot_key, msg, sink)

    def _handle_event(self, bot_key: str, msg: InboundMessage) -> None:
        """处理事件帧：反馈回执落日志；其余事件不回复。

        事件帧一律不回复：实测 `enter_chat` 的 req_id 不能用于 `reply_stream`
        （`errcode=846605`），欢迎语须走 `reply_welcome`（见 sdk-facts 的「其他实测事实」）。

        Args:
            bot_key: 机器人别名
            msg: 入站事件
        """
        if msg.event_type != WECOM_EVENT_FEEDBACK:
            logger.debug(
                "[wecom] event ignored bot_key={} event_type={}",
                encode_value(bot_key),
                msg.event_type,
            )
            return
        feedback_id = extract_feedback_id(msg.raw)
        if feedback_id is None:
            logger.warning(
                "[wecom] feedback frame without id bot_key={} msgid={}",
                encode_value(bot_key),
                msg.msgid,
            )
            return
        logger.info(
            "[wecom] feedback received bot_key={} trace_id={} msgid={}",
            encode_value(bot_key),
            encode_value(feedback_id),
            msg.msgid,
        )

    async def _run_turn(
        self, bot_key: str, msg: InboundMessage, sink: ReplySink
    ) -> None:
        """跑一轮：派生标识 → 生成并设置 trace_id → 起生成 → 投影。

        Args:
            bot_key: 机器人别名
            msg: 入站消息（`text` 非空）
            sink: 回复出口
        """
        session_id = derive_session_id(
            bot_key=bot_key,
            chattype=msg.chattype,
            chatid=msg.chatid,
            from_userid=msg.from_userid,
        )
        user_id = derive_user_id(msg.from_userid)
        trace_id = new_trace_id()
        token = current_trace_id.set(trace_id)
        try:
            logger.info(
                "[wecom] inbound bot_key={} msgid={} chattype={} session_id={}"
                " trace_id={} text_len={}",
                encode_value(bot_key),
                msg.msgid,
                msg.chattype,
                encode_value(session_id),
                encode_value(trace_id),
                len(msg.text or ""),
            )
            svc = await self._get_service()
            handle = await self._start_turn(
                svc,
                session_id=session_id,
                kb_id=self._kb_id,
                query=msg.text,
                user_id=user_id,
                title=build_session_title(
                    bot_key=bot_key,
                    chattype=msg.chattype,
                    chatid=msg.chatid,
                    from_userid=msg.from_userid,
                ),
            )
        except turn_runner.TurnBusy:
            logger.warning(
                "[wecom] session busy bot_key={} session_id={} trace_id={}",
                encode_value(bot_key),
                encode_value(session_id),
                encode_value(trace_id),
            )
            await sink.reply_stream(
                wecom_channel.WeComChannelTexts.BUSY_TEXT, finish=True
            )
            return
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[wecom] turn start failed bot_key={} session_id={} err={}",
                encode_value(bot_key),
                encode_value(session_id),
                e,
            )
            await sink.reply_stream(WeComPresenterTexts.ERROR_TEXT, finish=True)
            return
        finally:
            current_trace_id.reset(token)

        presenter = WeComPresenter(sink, trace_id)
        await presenter.run(
            self._tap(handle.events, session_id=session_id, from_userid=msg.from_userid)
        )

    async def _tap(
        self,
        events: AsyncIterator[SSEEvent],
        *,
        session_id: str,
        from_userid: str,
    ) -> AsyncIterator[SSEEvent]:
        """旁路事件流：澄清挂起时登记"会话 → 触发者"，其余原样透传。

        Args:
            events: 上游结构化事件流
            session_id: 本会话标识
            from_userid: 触发者 userid

        Yields:
            原样透传的事件
        """
        async for event in events:
            if isinstance(event, SSEAskUserEvent):
                self._triggers.put(session_id, from_userid)
                logger.info(
                    "[wecom] clarify pending registered session_id={} trace_id={}",
                    encode_value(session_id),
                    encode_value(current_trace_id.get() or ""),
                )
            yield event
