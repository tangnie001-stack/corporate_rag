"""fork 子代理的工具面筛选 —— 主 agent 工具池 ∩ 只读约束 ∩ 声明收窄（design D7/D8）。

口径（顺序即语义，勿调换）：
1. **先减禁用集**：`FORK_FORBIDDEN_TOOLS`（ask_user / delegate_task）与
   `FORK_EXCLUSIVE_TOOL_PREFIXES`（task_*）永不下发，**即使 skill 显式声明**
   （禁用集优先于白名单）。
2. **再按只读性筛**：非只读工具默认不下发；仅当 skill 在 `allowed-tools` 里**显式声明**
   该工具时才放行（白名单退为"写权限的例外通道"）。表中**缺项**按非只读处理（fail-safe）。
3. **`allowed-tools` 是收窄项**：不声明即**不收窄**（继承只读面）；声明了才取交集。
4. 执行者预设声明 `tools` 时再取交集。

**空表极性（design D7）**：`readonly_map()` 为空（工具尚未注册）时 fork 侧
**fail-closed**——一个都不下发。这与 `invocation.derive_invocation_flags` 对空表的
**fail-open** 极性**相反且都是有意为之**：同一张表的两个消费者失败代价不同——双轴推导空表时
不锁只是少了一层保护，而 fork 侧空表时"按只读放行"会把写权限下发给子代理。
**不得为"统一"而改掉任一侧的极。**
"""

import warnings

from langchain_core.tools import BaseTool

from src.config.const import FORK_EXCLUSIVE_TOOL_PREFIXES, FORK_FORBIDDEN_TOOLS


def _is_forbidden(name: str) -> bool:
    """禁用集判定：精确名（ask_user / delegate_task）+ 主 agent 专属前缀（task_*）。

    Args:
        name: 工具名

    Returns:
        True 表示该工具永不下发子代理（优先于白名单与只读放行）
    """
    if name in FORK_FORBIDDEN_TOOLS:
        return True
    for prefix in FORK_EXCLUSIVE_TOOL_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def select_fork_tools(
    allowed: list[str],
    available: list,
    executor_tools: list[str] | None = None,
    tool_readonly: dict[str, bool] | None = None,
) -> list:
    """按 design D7 口径筛选可交给子代理的工具（四步顺序见模块 docstring）。

    Args:
        allowed: skill 的 allowed-tools（**空 = 不收窄**；非空 = 收窄为交集，
            并作为非只读工具的例外放行通道）
        available: 本次启用的工具对象（LangChain BaseTool）
        executor_tools: 执行者预设声明的工具名（空/None = 不再收窄）
        tool_readonly: 工具名 -> 是否只读（readonly_map()）；空/None 视为表未填充

    Returns:
        过滤后的工具列表（保持 available 原顺序）；空表时返回空列表（fail-closed）
    """
    readonly: dict[str, bool] = {}
    if tool_readonly is not None:
        readonly = tool_readonly
    if not readonly:
        warnings.warn(
            "工具只读表为空（工具尚未注册），fork 子代理工具面按 fail-closed 处理：不下发任何工具"
        )
        return []
    declared = set(allowed)
    executor_allowed: set[str] = set()
    if executor_tools:
        executor_allowed = set(executor_tools)
    picked = []
    for tool in available:
        if not isinstance(tool, BaseTool):
            continue
        if _is_forbidden(tool.name):
            continue
        is_readonly = readonly.get(tool.name, False)
        if not is_readonly and tool.name not in declared:
            continue
        if declared and tool.name not in declared:
            continue
        if executor_allowed and tool.name not in executor_allowed:
            continue
        picked.append(tool)
    return picked
