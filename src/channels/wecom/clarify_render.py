"""把澄清问题清单渲染成企微可读文本块（design D14；投影层只调用、不实现）。

纯函数、无副作用：便于单测，也让 `presenter.py` 保持体量（红线 ≤400 行）。
"""

from __future__ import annotations

from src.config.wecom_channel import WeComChannelTexts


def render_questions(questions: list) -> str:
    """渲染问题清单；无有效问题时返回空串。

    Args:
        questions: SSEAskUserEvent.questions（元素含 question/options）

    Returns:
        形如 "需要您补充信息…\\n1. 选哪个口径？（可选：营收、毛利）\\n…" 的文本块
    """
    lines: list[str] = []
    index = 0
    for item in questions:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        if not question:
            continue
        index += 1
        line = WeComChannelTexts.CLARIFY_ITEM.format(index=index, question=question)
        options = item.get("options") or []
        if options:
            joined = "、".join(str(option) for option in options)
            line = f"{line} {WeComChannelTexts.CLARIFY_OPTIONS.format(options=joined)}"
        lines.append(line)
    if not lines:
        return ""
    lines.append(WeComChannelTexts.CLARIFY_HINT)
    return "\n".join([WeComChannelTexts.CLARIFY_HEADER, *lines])
