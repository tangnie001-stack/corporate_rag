"""工具只读声明的单一事实来源（进程级）。

工具只读性的事实写在**注册点**（rag_tools/web_tools 等），而 skill 加载发生在
AgentService 构造期——那时工具尚未构造（工具在 build_graph → make_rag_tools
内部创建），拿不到 ToolRegistry 实例。故用进程级声明表衔接两侧：
注册点 declare_readonly(...)，消费方（skill 双轴推导）readonly_map()。

与项目既有的进程级 pending_asks 注册表同构（进程内共享、启动期写入、运行期只读）。
"""

_TOOL_READONLY: dict[str, bool] = {}


def declare_readonly(name: str, readonly: bool) -> None:
    """登记工具只读性（由 ToolRegistry.register 转发调用）。

    Args:
        name: 工具名
        readonly: True=只读无外部副作用；False=写/改/删/发/外部调用
    """
    _TOOL_READONLY[name] = readonly


def readonly_map() -> dict[str, bool]:
    """返回 工具名 -> readonly 的映射副本（供 skill 双轴默认推导）。

    返回副本而非内部引用，避免调用方误改单一来源。
    """
    return dict(_TOOL_READONLY)
