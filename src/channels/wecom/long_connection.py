"""企微智能机器人长连接驱动：包住官方 aibot SDK，实现 ChannelDriver。

由本服务主动连企微（wss://openws.work.weixin.qq.com），无需公网回调地址、无加解密。
仅使用 SDK 的公开接口，不调用 run()（它自建事件循环，与 FastAPI 冲突）。
"""

from typing import Any

from aibot import WSClient, WSClientOptions, generate_req_id
from loguru import logger

from src.channels.base import MessageHandler
from src.channels.wecom.parse import parse_inbound
from src.core.logging import encode_value

# SDK 事件名：消息与事件共用同一处理器（入站 body 结构与回调一致）
_MESSAGE_EVENTS: tuple[str, ...] = (
    "message.text",
    "message.image",
    "message.mixed",
    "message.voice",
    "message.file",
)
_EVENT_EVENTS: tuple[str, ...] = (
    "event.enter_chat",
    "event.template_card_event",
    "event.feedback_event",
)

# 驱动级事件（不进业务 handler）：被更新的连接顶替时由服务端推送。
# 实测（见 docs/agents/wecom-sdk-facts.md 的 E4）：服务端先推该事件（此刻本连接 WS 无异常），
# 随后才关闭本连接；该关闭会触发 SDK 自动重连，而重连成功又顶掉对方 ⇒ 形成互踢循环。
# 故收到即主动断开（SDK 手动断开不触发重连）并交出 client 引用，不抢回。
_DISPLACED_EVENT: str = "event.disconnected_event"


class _WsSink:
    """ReplySink 实现：把回复转成 SDK 的流式回复调用。"""

    def __init__(self, client: WSClient, frame: dict[str, Any], stream_id: str):
        """初始化。

        Args:
            client: 官方 SDK 客户端
            frame: 触发本次回复的入站帧（SDK 回复需透传）
            stream_id: 本条消息的流式 id（生成一次，多次回复复用）
        """
        self._client = client
        self._frame = frame
        self._stream_id = stream_id

    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        """发送流式回复。

        Args:
            content: 本帧内容（累积全文）
            finish: 是否结束流式消息
            feedback: 反馈信息（仅首帧设置；None 表示不带）
        """
        await self._client.reply_stream(
            self._frame, self._stream_id, content, finish, feedback=feedback
        )


class LongConnectionDriver:
    """长连接驱动。"""

    name = "wecom_long_connection"

    def __init__(self, bot_id: str, secret: str, handler: MessageHandler):
        """初始化。

        Args:
            bot_id: 智能机器人 BotID
            secret: 长连接专用 Secret
            handler: 业务处理（吃入站事件，用 sink 回复）
        """
        self._bot_id = bot_id
        self._secret = secret
        self._handler = handler
        self._client: WSClient | None = None
        self._displaced: bool = False

    @property
    def is_displaced(self) -> bool:
        """该台是否已被更新的连接顶替。

        被顶后驱动已主动断开且不再抢回（防互踢）；就绪口径与启动锚点对它的反映
        见 change `wecom-agent-bridge` 的 D11 与 tasks 5.1。
        """
        return self._displaced

    async def start(self) -> None:
        """建立长连接并注册事件处理器。"""
        # 已启动则直接返回：重复构造会覆盖 self._client，旧连接的 WebSocket
        # 与收帧循环不会 disconnect，仍会向同一 handler 派帧（重复处理 + 资源泄漏）。
        if self._client is not None:
            return
        client = WSClient(WSClientOptions(bot_id=self._bot_id, secret=self._secret))
        handler = self._make_handler(client)
        for event in _MESSAGE_EVENTS + _EVENT_EVENTS:
            client.on(event, handler)
        client.on(_DISPLACED_EVENT, self._make_displaced_handler(client))
        await client.connect()
        self._client = client

    async def stop(self) -> None:
        """断开长连接。"""
        if self._client is None:
            return
        self._client.disconnect()
        self._client = None

    def _make_displaced_handler(self, client: WSClient):
        """构造"被顶替"处理器：主动断开且不抢回，避免与顶替者互踢。

        幂等：重复到达直接返回。断开后交出 `_client` 引用，使后续 `stop()` 成为
        no-op、并允许运维侧显式重启该台（重新 `start()` 才会再次抢回归属）。
        """

        async def _handle_displaced(frame: dict[str, Any]) -> None:
            if self._displaced:
                return
            self._displaced = True
            body = frame.get("body", {})
            logger.warning(
                "[wecom] bot displaced by newer connection bot_id={} msgid={}"
                "; disconnecting without reclaim",
                encode_value(self._bot_id),
                body.get("msgid", ""),
            )
            client.disconnect()
            if self._client is client:
                self._client = None

        return _handle_displaced

    def _make_handler(self, client: WSClient):
        """构造 SDK 事件处理器：帧 → InboundMessage → 业务 handler。"""

        async def _handle(frame: dict[str, Any]) -> None:
            try:
                msg = parse_inbound(frame["body"])
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "[wecom] parse inbound failed bot_id={} err={}",
                    encode_value(self._bot_id),
                    e,
                )
                return
            sink = _WsSink(client, frame, generate_req_id("stream"))
            try:
                await self._handler(msg, sink)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "[wecom] handler failed bot_id={} msgid={} err={}",
                    encode_value(self._bot_id),
                    msg.msgid,
                    e,
                )

        return _handle
