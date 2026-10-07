"""企微智能机器人通道的编排：按 WECOM_BOT_MODE 装配驱动 + 注入默认 handler。

多机器人（长连接）：每台一个驱动，集中存放于驱动注册表；逐台装配/降级/关停。
回调保留为 legacy 单机器人（存于保留键 `callback`）。
本轮 handler 只打日志并回写死回复；后续替换为「调 agent 跑 RAG」。
"""

from loguru import logger

from src.channels.base import ChannelDriver, InboundMessage, ReplySink
from src.channels.wecom.callback import CallbackDriver
from src.channels.wecom.crypto import WeComCrypto
from src.channels.wecom.long_connection import LongConnectionDriver
from src.config import settings
from src.config.const import WECOM_REPLY_PLACEHOLDER
from src.config.wecom_bots import CALLBACK_BOT_KEY, load_wecom_bots
from src.core.logging import encode_value

# 接入模式取值
_MODE_CALLBACK = "callback"
_MODE_LONG_CONNECTION = "long_connection"

# 驱动注册表：键 = bot_key（长连接）或保留键 callback（回调 legacy）
_drivers: dict[str, ChannelDriver] = {}
# 反查表：aibotid(BotID) → bot_key；由 start() 一次性构建（handler 只读）
_bot_key_by_aibotid: dict[str, str] = {}


def _resolve_bot_key(aibotid: str) -> str | None:
    """把入站 aibotid 映射到 bot_key；未知返回 None。

    回调 legacy 模式下唯一驱动即回调驱动，固定返回保留键。
    """
    if settings.WECOM_BOT_MODE == _MODE_CALLBACK:
        return CALLBACK_BOT_KEY
    return _bot_key_by_aibotid.get(aibotid)


async def _default_handler(msg: InboundMessage, sink: ReplySink) -> None:
    """默认 handler：按 bot_key 分发；消息回写死流式回复，事件不回包。"""
    bot_key = _resolve_bot_key(msg.aibotid)
    if bot_key is None:
        logger.warning(
            "[wecom] inbound from unknown bot aibotid={}", encode_value(msg.aibotid)
        )
        return

    text_len = 0
    if msg.text:
        text_len = len(msg.text)
    logger.info(
        "[wecom] inbound bot_key={} msgid={} chattype={} msgtype={} event_type={} text_len={}",
        encode_value(bot_key),
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
    """callback 模式启动期校验：凭证非法即抛，不留请求期降级。"""
    if not settings.WECOM_BOT_TOKEN:
        raise RuntimeError("WECOM_BOT_TOKEN 未配置")
    if len(settings.WECOM_BOT_ENCODING_AES_KEY) != 43:
        raise RuntimeError("WECOM_BOT_ENCODING_AES_KEY 必须为 43 位")


async def start() -> None:
    """启动通道：enabled 时按 mode 装配驱动，否则 no-op。"""
    global _drivers, _bot_key_by_aibotid
    if not settings.WECOM_BOT_ENABLED:
        return
    mode = settings.WECOM_BOT_MODE

    if mode == _MODE_CALLBACK:
        _validate_credentials()
        crypto = WeComCrypto(
            settings.WECOM_BOT_TOKEN,
            settings.WECOM_BOT_ENCODING_AES_KEY,
            settings.WECOM_BOT_RECEIVE_ID,
        )
        _drivers = {CALLBACK_BOT_KEY: CallbackDriver(crypto, _default_handler)}
        _bot_key_by_aibotid = {}
        return

    if mode != _MODE_LONG_CONNECTION:
        raise RuntimeError(f"未知 WECOM_BOT_MODE: {mode}")

    # 解析/校验置于逐台 try/except 之外：配置错误必须 fail-fast，不得被降级吞掉
    bots = load_wecom_bots()
    if not bots:
        raise RuntimeError("WECOM_BOT_MODE=long_connection 但 WECOM_BOTS 为空")

    _bot_key_by_aibotid = {bot.bot_id: bot.key for bot in bots}
    _drivers = {}
    for bot in bots:
        driver = LongConnectionDriver(bot.bot_id, bot.secret, _default_handler)
        try:
            await driver.start()
        except Exception as e:  # noqa: BLE001
            # 通道可选：单台连接失败不拖垮其余台，也不阻塞应用启动
            logger.warning(
                "[wecom] bot connect failed, skipped bot_key={} err={}",
                encode_value(bot.key),
                e,
            )
            continue
        _drivers[bot.key] = driver

    logger.info("[wecom] bots connected n={} total={}", len(_drivers), len(bots))


async def stop() -> None:
    """停止通道：逐台断开，单台失败不阻塞其余，幂等。"""
    global _drivers, _bot_key_by_aibotid
    for bot_key, driver in list(_drivers.items()):
        try:
            await driver.stop()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[wecom] bot stop failed bot_key={} err={}",
                encode_value(bot_key),
                e,
            )
    _drivers = {}
    _bot_key_by_aibotid = {}


def get_driver(bot_key: str) -> ChannelDriver:
    """取指定机器人的驱动；未命中抛 RuntimeError。"""
    driver = _drivers.get(bot_key)
    if driver is None:
        raise RuntimeError(f"wecom driver 未启动或不存在: {bot_key}")
    return driver


def get_callback_driver() -> CallbackDriver:
    """取回调驱动；非 callback 模式或未启动时抛 RuntimeError。"""
    if settings.WECOM_BOT_MODE != _MODE_CALLBACK:
        raise RuntimeError("当前非 callback 模式")
    driver = _drivers.get(CALLBACK_BOT_KEY)
    if driver is None:
        raise RuntimeError("wecom driver 未启动")
    if not isinstance(driver, CallbackDriver):
        # 契约要求统一抛 RuntimeError（路由按此捕获），故不换成 TypeError
        raise RuntimeError("当前驱动不是 CallbackDriver")  # noqa: TRY004
    return driver
