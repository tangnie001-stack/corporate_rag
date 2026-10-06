"""企微智能机器人「URL 回调」驱动：验签 / 解密 / 解析 / 加密回包。

传输层独占职责——路由只透传原始 query 与 body，本驱动返回最终 Response。
"""

import json
import uuid
from dataclasses import dataclass
from urllib.parse import unquote

from loguru import logger
from starlette.responses import JSONResponse, PlainTextResponse, Response

from src.channels.base import MessageHandler
from src.channels.wecom.crypto import WeComCrypto
from src.channels.wecom.parse import InboundParseError, parse_inbound


@dataclass(frozen=True)
class OutboundReply:
    """一条待发送的被动回复。"""

    kind: str  # 回复类型；目前仅 "stream"
    content: str  # 回复正文（支持 markdown）
    finish: bool  # 流式是否结束


def _parse_query(raw_query: str) -> dict[str, str]:
    """解析原始 query 字符串，**保留 '+'**。

    不能用 urllib.parse.parse_qsl / unquote_plus —— 它们把裸 '+' 解成空格，
    而 echostr 是 base64、可能含 '+'，会导致解密失败。unquote 只做百分号解码。
    """
    result: dict[str, str] = {}
    for pair in raw_query.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        result[unquote(key)] = unquote(value)
    return result


class _CallbackSink:
    """收集本轮回复；回调模式只支持首次同步回包。"""

    def __init__(self) -> None:
        self.reply: OutboundReply | None = None

    async def reply_stream(self, content: str, finish: bool) -> None:
        """记录一条流式回复。"""
        self.reply = OutboundReply(kind="stream", content=content, finish=finish)


class CallbackDriver:
    """企微回调驱动。"""

    name = "wecom_callback"

    def __init__(self, crypto: WeComCrypto, handler: MessageHandler):
        """初始化。

        Args:
            crypto: 加解密器
            handler: 业务处理（吃入站事件，用 sink 回复）
        """
        self._crypto = crypto
        self._handler = handler

    async def start(self) -> None:
        """回调无需建连，no-op（为长连接对称而留）。"""

    async def stop(self) -> None:
        """回调无需断连，no-op。"""

    def verify(self, raw_query: str) -> Response:
        """处理 URL 有效性验证（GET）。"""
        q = _parse_query(raw_query)
        timestamp = q.get("timestamp", "")
        nonce = q.get("nonce", "")
        echostr = q.get("echostr", "")
        if self._crypto.signature(timestamp, nonce, echostr) != q.get(
            "msg_signature", ""
        ):
            return PlainTextResponse("invalid signature", status_code=403)
        return PlainTextResponse(self._crypto.decrypt(echostr))

    async def handle_message(self, raw_query: str, raw_body: bytes) -> Response:
        """处理消息/事件回调（POST）。"""
        q = _parse_query(raw_query)
        timestamp = q.get("timestamp", "")
        nonce = q.get("nonce", "")

        try:
            body = json.loads(raw_body)
            encrypt = body["encrypt"]
        except (ValueError, KeyError, TypeError):
            return PlainTextResponse("bad request", status_code=400)

        if self._crypto.signature(timestamp, nonce, encrypt) != q.get(
            "msg_signature", ""
        ):
            return PlainTextResponse("invalid signature", status_code=403)

        try:
            plain = json.loads(self._crypto.decrypt(encrypt))
            msg = parse_inbound(plain)
        except (ValueError, InboundParseError):
            return PlainTextResponse("bad request", status_code=400)

        sink = _CallbackSink()
        try:
            await self._handler(msg, sink)
        except Exception as e:  # noqa: BLE001
            # 不向企微抛错：返回空体 200，避免重试风暴
            logger.error("[wecom] handler failed msgid={} err={}", msg.msgid, e)
            return Response(status_code=200)

        if sink.reply is None:
            return Response(status_code=200)
        return self._build_reply(sink.reply, timestamp, nonce)

    def _build_reply(
        self, reply: OutboundReply, timestamp: str, nonce: str
    ) -> Response:
        """把 OutboundReply 加密为企微要求的回包体。"""
        stream_id = uuid.uuid4().hex
        plain = json.dumps(
            {
                "msgtype": "stream",
                "stream": {
                    "id": stream_id,
                    "content": reply.content,
                    "finish": reply.finish,
                },
            },
            ensure_ascii=False,
        )
        encrypt = self._crypto.encrypt(plain)
        # 复用请求的 timestamp/nonce，对回包密文签名
        return JSONResponse(
            {
                "encrypt": encrypt,
                "msgsignature": self._crypto.signature(timestamp, nonce, encrypt),
                "timestamp": int(timestamp),
                "nonce": nonce,
            }
        )
