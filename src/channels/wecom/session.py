"""企微会话 / 用户标识派生（design D2）。

`session_id` / `user_id` 都会写进 `String(36)` 的列（sessions / conversation_history /
feedback），故必须派生为 36 字符的确定性标识；可读信息（bot_key / chatid / userid）
只进日志与 `sessions.title`。
"""

from __future__ import annotations

import uuid

from src.config.const import WECOM_NS


def derive_session_id(
    *, bot_key: str, chattype: str, chatid: str | None, from_userid: str
) -> str:
    """派生会话标识：群聊按 `chatid`、单聊按 `from_userid`。

    Args:
        bot_key: 机器人别名（三台机器人同一用户不共会话）
        chattype: 会话类型（"group" / "single"；非 group 一律按单聊）
        chatid: 群聊会话 id；单聊为 None
        from_userid: 触发者 userid

    Returns:
        36 字符 UUIDv5 字符串（同群/同人稳定）
    """
    if chattype == "group" and chatid:
        scope = "group"
        ident = chatid
    else:
        scope = "single"
        ident = from_userid
    return str(uuid.uuid5(WECOM_NS, f"wecom|{bot_key}|{scope}|{ident}"))


def derive_user_id(from_userid: str) -> str:
    """派生用户标识（`sessions.user_id` 契约亦为 UUID）。

    Args:
        from_userid: 触发者 userid（非超管场景为密文；密文同样稳定）

    Returns:
        36 字符 UUIDv5 字符串
    """
    return str(uuid.uuid5(WECOM_NS, f"wecom-user|{from_userid}"))


def build_session_title(
    *, bot_key: str, chattype: str, chatid: str | None, from_userid: str
) -> str:
    """构造可读会话标题（落 `sessions.title`，仅首次落库生效）。

    Args:
        bot_key: 机器人别名
        chattype: 会话类型
        chatid: 群聊会话 id
        from_userid: 触发者 userid

    Returns:
        形如 `[企微·finance] 群聊 CHAT9` 的标题
    """
    if chattype == "group":
        scope = "群聊"
        ident = chatid or ""
    else:
        scope = "单聊"
        ident = from_userid
    return f"[企微·{bot_key}] {scope} {ident}"
