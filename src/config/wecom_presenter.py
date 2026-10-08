"""企微流式投影层的可调参数与用户可见文案（design D4/D6/D9/D16/D18）。

归属：既不放 `settings.py`（385 行、逼近 400 行红线），也不放 `const.py`
（440 行、已越线），而独立成 config 子模块——与 `wecom_bots.py` 同例。
本模块文案不经 SSE 管线，故不属 `SSEInteractionTexts` 的 SSE 交互文案。
"""

import os

# ── 可调参数（Spike E6/E7/E10 实测后可经环境变量调整，不必改代码）──

# 发送最小间隔（秒）：企微每帧等 ack 且同 req_id 串行，逐 token 发会堆队列
MIN_SEND_INTERVAL_SECONDS: float = float(
    os.getenv("WECOM_PRESENTER_MIN_INTERVAL_SECONDS", "0.1")
)
# 承载正文的中间帧数上限（保活帧与占位帧不计入）
MAX_INTERMEDIATE_FRAMES: int = int(os.getenv("WECOM_PRESENTER_MAX_FRAMES", "85"))
# 单流累计正文长度上限（超出保留尾部）
MAX_STREAM_CHARS: int = int(os.getenv("WECOM_PRESENTER_MAX_CHARS", "200000"))
# 保活间隔（秒）：须小于「首帧起 6 分钟」的收尾时限
KEEPALIVE_INTERVAL_SECONDS: float = float(
    os.getenv("WECOM_PRESENTER_KEEPALIVE_SECONDS", "240")
)
# 首帧最迟时间（秒）：超过则先发占位帧，不空等到超时
FIRST_FRAME_TIMEOUT_SECONDS: float = float(
    os.getenv("WECOM_PRESENTER_FIRST_FRAME_TIMEOUT", "2")
)
# 终态 trace_id footer 开关
TRACE_FOOTER_ENABLED: bool = os.getenv(
    "WECOM_TRACE_FOOTER_ENABLED", "true"
).lower() in ("1", "true", "yes")
# 首帧反馈标识是否可用：Spike E10 实测**可用**（回执帧 `body.event.feedback_event.id`
# 原样回传，见 docs/agents/wecom-sdk-facts.md），故默认开启；置 false 则退化为 footer+日志两路
FEEDBACK_ID_ENABLED: bool = os.getenv("WECOM_FEEDBACK_ID_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
# 终态 footer 的有效开关：反馈标识不可用时强制开启（footer 成唯一人工可读路）
TRACE_FOOTER_EFFECTIVE: bool = TRACE_FOOTER_ENABLED or not FEEDBACK_ID_ENABLED


class WeComPresenterTexts:
    """企微投影层对用户可见的文案（不散落在投影逻辑里）。"""

    # 占位首帧默认文案：首个状态事件到达前使用
    PLACEHOLDER_TEXT: str = "正在处理，请稍候…"
    # 终态兜底文案：整轮无正文时使用（避免空白关闭或悬挂流）
    FALLBACK_TEXT: str = "抱歉，本次未能生成回答，请重试或转人工咨询。"
    # 脱敏错误文案：原始异常只进日志，不外泄给用户
    ERROR_TEXT: str = "暂时无法回答，请稍后重试或转人工咨询。"
    # 转人工提示：拒答（abstention）时追加到终态正文
    ABSTENTION_TEXT: str = "未在文档中找到相关数据，可尝试转人工咨询。"
    # 文末来源段标题
    SOURCES_TITLE: str = "参考来源"
    # 终态 footer 模板（用 .format(trace_id) 填充）
    TRACE_FOOTER_TEMPLATE: str = "trace_id: {}"
