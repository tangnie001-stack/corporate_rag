"""skill 正文渲染 — 任务占位符替换。

skill 正文（inline_prompt / fork_body）可含任务占位符：$ARGUMENTS（当前写法）
或 {task}（旧写法，向后兼容）。渲染时替换为主 agent 委托的任务文本；无占位符
时原样返回。
"""

from src.config.const import SKILL_TASK_PLACEHOLDERS


def render_skill_body(body: str, task: str) -> str:
    """把 skill 正文中的任务占位符替换为任务文本。

    Args:
        body: skill 正文（inline_prompt 或 fork_body）
        task: 主 agent 委托的任务文本

    Returns:
        替换后的正文；无占位符时原样返回
    """
    for placeholder in SKILL_TASK_PLACEHOLDERS:
        body = body.replace(placeholder, task)
    return body
