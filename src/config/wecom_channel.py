"""企微**通道层**的参数与用户可见文案（design D10/D14）。

与投影层参数分档：本模块放通道编排关心的时间窗与容量；投影层的节流/上限/保活
在 `src/config/wecom_presenter.py`。两者都独立于 `settings.py`（385 行，逼近红线）。
"""

import os

# ── msgid 去重（D10）──
# 去重窗口（秒）：窗口内同 msgid 视为重推并丢弃
DEDUP_TTL_SECONDS: float = float(os.getenv("WECOM_DEDUP_TTL_SECONDS", "600"))
# 去重表容量上限（超出淘汰最旧）
DEDUP_CAPACITY: int = int(os.getenv("WECOM_DEDUP_CAPACITY", "500"))

# ── 澄清"会话 → 触发者"登记（D14）──
# 登记存活时间（秒）：须 ≥ ask_user 等待超时（ASK_USER_TIMEOUT=120s），留清理余量
TRIGGER_MAP_TTL_SECONDS: float = float(
    os.getenv("WECOM_TRIGGER_MAP_TTL_SECONDS", "300")
)
# 登记表容量上限（超出淘汰最旧）
TRIGGER_MAP_CAPACITY: int = int(os.getenv("WECOM_TRIGGER_MAP_CAPACITY", "500"))


class WeComChannelTexts:
    """企微通道层对用户可见的文案。"""

    # 同会话已有进行中的生成时回给用户的提示（替站点 409）
    BUSY_TEXT: str = "正在处理上一条消息，请稍候再发。"
    # 非文本消息（图片/语音/文件等）的提示：管线只吃文本 query
    UNSUPPORTED_TEXT: str = "暂只支持文字提问，请把问题打成文字发给我。"
    # 澄清问题块：头部 + 每条问题模板（{index}/{question}）与选项行模板（{options}）
    CLARIFY_HEADER: str = "需要您补充信息（请直接回复本条消息）："
    CLARIFY_ITEM: str = "{index}. {question}"
    CLARIFY_OPTIONS: str = "（可选：{options}）"
    CLARIFY_HINT: str = "请回复上面的问题；超时未回复我会按已有信息继续。"
    # 澄清答复无法解析时的提示（**不消耗**挂起，用户可重答）
    CLARIFY_INVALID_TEXT: str = (
        "没看懂您的回复。请按上面的编号逐条作答，或直接回复选项文字。"
    )
