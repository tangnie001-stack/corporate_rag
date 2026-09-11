"""工具注册表 — 可插拔工具管理（注册/启停/依赖注入）。

为 MCP 接入与未来 subagent 工具预留统一入口；agent 循环只消费 enabled_tools()。
fn 用 Any 而非 Callable：LangChain BaseTool 不满足 pyright 对 Callable 的结构匹配，
注册表存异构可调用对象（LangChain tool / 未来 MCP 工具），类型由消费方约束。
"""

from dataclasses import dataclass, field
from typing import Any

from src.agents.tools.readonly import declare_readonly


@dataclass
class ToolEntry:
    """注册表条目。

    fn: 可调用工具（LangChain tool 或装饰器产物）
    deps: 依赖注入 dict（工具闭包需要的外部依赖）
    enabled: 是否启用（停用不出现在 LLM 可见列表）
    readonly: 是否只读无外部副作用（skill 双轴默认推导的事实来源，True=只读）
    """

    fn: Any
    deps: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    readonly: bool = True


class ToolRegistry:
    """按工具名管理注册条目，返回当前启用工具列表。"""

    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}

    def register(
        self,
        name: str,
        fn: Any,
        deps: dict | None = None,
        enabled: bool = True,
        readonly: bool = True,
    ) -> None:
        """注册一个工具（同时把只读性写入进程级声明表）。

        Args:
            name: 工具名（LLM 可见）
            fn: 工具可调用对象
            deps: 依赖注入 dict
            enabled: 初始是否启用
            readonly: 是否只读无外部副作用（写/改/删/发/外部调用应传 False）
        """
        self._entries[name] = ToolEntry(
            fn=fn, deps=deps or {}, enabled=enabled, readonly=readonly
        )
        declare_readonly(name, readonly)

    def readonly_map(self) -> dict[str, bool]:
        """返回本注册表内 工具名 -> readonly 映射。"""
        return {name: entry.readonly for name, entry in self._entries.items()}

    def unregister(self, name: str) -> None:
        """注销工具（不存在时静默）。"""
        self._entries.pop(name, None)

    def set_enabled(self, name: str, enabled: bool) -> None:
        """按工具粒度启停。"""
        if name in self._entries:
            self._entries[name].enabled = enabled

    def enabled_tools(self) -> list[Any]:
        """返回当前启用工具的可调用列表（供 LLM bind_tools）。"""
        return [e.fn for e in self._entries.values() if e.enabled]

    def get(self, name: str) -> Any:
        """按名取工具（不存在抛 KeyError）。"""
        return self._entries[name].fn
