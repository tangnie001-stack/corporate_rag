"""入站解析：解密后的 JSON 明文 → InboundMessage（回调 / 长连接共用）。"""

from src.channels.base import InboundMessage


class InboundParseError(ValueError):
    """入站报文缺失必填字段或结构非法。"""


def parse_inbound(plain: dict) -> InboundMessage:
    """把企微回调明文解析为统一入站事件。

    Args:
        plain: 解密后的 JSON 明文 dict

    Returns:
        InboundMessage

    Raises:
        InboundParseError: 缺少 msgid 等必填字段
    """
    msgid = plain.get("msgid")
    if not msgid:
        raise InboundParseError("missing msgid")

    msgtype = plain.get("msgtype", "")
    text: str | None = None
    event_type: str | None = None
    if msgtype == "event":
        event = plain.get("event") or {}
        event_type = event.get("eventtype")
    elif msgtype == "text":
        text_obj = plain.get("text") or {}
        text = text_obj.get("content")

    from_obj = plain.get("from") or {}
    return InboundMessage(
        msgid=msgid,
        aibotid=plain.get("aibotid", ""),
        chatid=plain.get("chatid"),
        chattype=plain.get("chattype", ""),
        from_userid=from_obj.get("userid", ""),
        msgtype=msgtype,
        text=text,
        event_type=event_type,
        raw=plain,
    )
