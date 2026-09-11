"""fork 子代理的工具筛选 —— 执行者 tools ∩ skill allowed-tools。

交集口径（design D7）：allowed-tools 为空 → 零工具；执行者预设声明 tools 时再收窄
到两者交集；名字对不上的白名单项忽略（不抛，避免一个笔误打断整次委派）。
"""

from langchain_core.tools import BaseTool


def select_fork_tools(
    allowed: list[str],
    available: list,
    executor_tools: list[str] | None = None,
) -> list:
    """按白名单筛选可交给子代理的工具。

    Args:
        allowed: skill 的 allowed-tools（空 = 零工具）
        available: 当前注册表可用的工具对象（LangChain BaseTool）
        executor_tools: 执行者预设声明的工具名（空/None = 不再收窄）

    Returns:
        过滤后的工具列表（保持 available 原顺序）
    """
    if not allowed:
        return []
    names = set(allowed)
    if executor_tools:
        names &= set(executor_tools)
    picked = []
    for tool in available:
        if not isinstance(tool, BaseTool):
            continue
        if tool.name in names:
            picked.append(tool)
    return picked
