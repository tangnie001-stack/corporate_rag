"""企微智能机器人回调通道的编排：装配驱动 + 注入默认 handler。

本轮 handler 只打日志并回写死回复；后续替换为「调 agent 跑 RAG」。
"""

from loguru import logger

from src.channels.base import InboundMessage, ReplySink
from src.channels.wecom.callback import CallbackDriver
from src.channels.wecom.crypto import WeComCrypto
from src.config import settings
from src.config.const import WECOM_REPLY_PLACEHOLDER

# 单例驱动；由 start() 在 enabled 时构造
_driver: CallbackDriver | None = None


async def _default_handler(msg: InboundMessage, sink: ReplySink) -> None:
    """默认 handler：打结构性日志；消息回写死流式回复，事件不回包。"""
    text_len = 0
    if msg.text:
        text_len = len(msg.text)
    logger.info(
        "[wecom] inbound msgid={} chattype={} msgtype={} event_type={} text_len={}",
        msg.msgid,
        msg.chattype,
        msg.msgtype,
        msg.event_type,
        text_len,
    )
    if settings.WECOM_BOT_LOG_CONTENT and msg.text:
        logger.debug("[wecom] content={}", msg.text)

    if msg.msgtype == "event":
        return
    await sink.reply_stream(WECOM_REPLY_PLACEHOLDER, finish=True)


def _validate_credentials() -> None:
    """enabled 时的启动期校验：凭证非法即抛，不留请求期降级。"""
    if not settings.WECOM_BOT_TOKEN:
        raise RuntimeError("WECOM_BOT_TOKEN 未配置")
    if len(settings.WECOM_BOT_ENCODING_AES_KEY) != 43:
        raise RuntimeError("WECOM_BOT_ENCODING_AES_KEY 必须为 43 位")


async def start() -> None:
    """启动通道：enabled 时构造并校验驱动，否则 no-op。"""
    global _driver
    if not settings.WECOM_BOT_ENABLED:
        return
    _validate_credentials()
    crypto = WeComCrypto(
        settings.WECOM_BOT_TOKEN,
        settings.WECOM_BOT_ENCODING_AES_KEY,
        settings.WECOM_BOT_RECEIVE_ID,
    )
    _driver = CallbackDriver(crypto, _default_handler)


async def stop() -> None:
    """停止通道。"""
    global _driver
    _driver = None


def get_driver() -> CallbackDriver:
    """取当前驱动；未启动时抛 RuntimeError。"""
    if _driver is None:
        raise RuntimeError("wecom driver 未启动")
    return _driver
