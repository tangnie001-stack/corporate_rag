"""skill 双轴调用控制的默认推导。

依据 `allowed-tools` ⊕ 工具 `readonly` 推导 user_invocable / disable_model_invocation：
- allowed-tools 为空 → 双通道开放
- 全为只读工具 → 双通道开放
- 含任一非只读工具 → 默认 `disable_model_invocation=True`（fail-safe）
- **工具只读表为空 → 无法判断，fail-open 不锁**（见下）

极性取 fail-safe：写类内容的"模型自动调用"等于替用户做授权决定，故默认锁模型端；
作者要放权须显式写 `disable-model-invocation: false`。

**空表为何必须 fail-open**：工具只读表由注册点写入，而 skill 可能在工具注册之前加载
（`AgentService` 构造期工具尚未构造）。若空表也按"未注册工具 = 写类"处理，则**所有**带
`allowed-tools` 的 skill 会被静默锁死模型端；反之表非空时，未命中的工具名才按写类处理
（用于挡住拼错的工具名）。
"""


def derive_invocation_flags(
    allowed_tools: list[str], tool_readonly: dict[str, bool]
) -> tuple[bool, bool]:
    """按工具只读性推导双轴默认值。

    Args:
        allowed_tools: skill 声明的工具白名单（已归一化为 list）
        tool_readonly: 工具名 -> 是否只读 的映射（readonly_map()）

    Returns:
        (user_invocable, disable_model_invocation)：
        未声明双轴时应写入 SkillRecord 的默认值。

    Note:
        表为空表示"工具尚未注册"，此时无法判断只读性 → fail-open（不锁模型端）；
        表非空但工具名未命中，按写类处理（fail-safe，防拼错工具名放开模型端）。
    """
    if not allowed_tools:
        return True, False
    if not tool_readonly:
        return True, False
    has_write_tool = False
    for tool_name in allowed_tools:
        readonly = tool_readonly.get(tool_name, False)
        if not readonly:
            has_write_tool = True
            break
    if has_write_tool:
        return True, True
    return True, False
