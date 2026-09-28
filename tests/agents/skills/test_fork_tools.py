"""fork 子代理工具面：继承只读面 − 禁用集 − 非只读（除非显式声明）∩ 声明收窄。"""

from unittest.mock import MagicMock

import pytest
from langchain_core.tools import tool

from src.agents.skills.fork_tools import select_fork_tools
from src.agents.tools.readonly import readonly_map
from src.agents.tools.task_tools import make_task_tools
from src.config.const import FORK_EXCLUSIVE_TOOL_PREFIXES, FORK_FORBIDDEN_TOOLS


@tool("retrieve_kb")
def _retrieve(query: str) -> str:
    """检索。"""
    return query


@tool("search_web")
def _search(query: str) -> str:
    """联网。"""
    return query


@tool("ask_user")
def _ask(question: str) -> str:
    """追问。"""
    return question


@tool("delegate_task")
def _delegate(skill: str) -> str:
    """委派。"""
    return skill


@tool("task_create")
def _task_create(title: str) -> str:
    """建任务。"""
    return title


@tool("write_doc")
def _write_doc(text: str) -> str:
    """写文件（写类工具的代表）。"""
    return text


# 只读表：检索/交互类只读（生产注册点即如此）；task_* 与 write_doc 不在表里
_RO = {"retrieve_kb": True, "search_web": True, "ask_user": True, "delegate_task": True}


def test_empty_allowed_inherits_readonly_face():
    """allowed-tools 为空 → 不收窄，继承只读面（D7 反转了"空 = 零工具"的旧语义）。"""
    picked = select_fork_tools([], [_retrieve, _search], None, _RO)
    assert [t.name for t in picked] == ["retrieve_kb", "search_web"]


def test_forbidden_tools_never_handed_over_even_when_declared():
    """禁用集优先于白名单：即使显式声明（且表里标只读）也不下发。"""
    picked = select_fork_tools(
        ["ask_user", "delegate_task"], [_ask, _delegate], None, _RO
    )
    assert picked == []


def test_task_prefix_blocked_even_when_readonly_and_declared():
    """task_* 前缀硬挡：表里标只读 + 显式声明 也不下发（D8 角色专属）。"""
    picked = select_fork_tools(
        ["task_create"], [_task_create], None, {**_RO, "task_create": True}
    )
    assert picked == []


def test_write_tool_not_handed_over_by_default():
    """非只读工具默认不下发。"""
    assert select_fork_tools([], [_write_doc], None, {**_RO, "write_doc": False}) == []


def test_write_tool_handed_over_when_explicitly_declared():
    """白名单退为例外通道：显式声明可放行非只读工具。"""
    picked = select_fork_tools(
        ["write_doc"], [_write_doc], None, {**_RO, "write_doc": False}
    )
    assert [t.name for t in picked] == ["write_doc"]


def test_missing_in_readonly_map_is_treated_as_write():
    """表中缺项按非只读处理（fail-safe）：未显式声明则不下发。"""
    assert select_fork_tools([], [_write_doc], None, _RO) == []


def test_allowed_narrows_to_intersection():
    """声明了白名单 → 收窄为交集。"""
    picked = select_fork_tools(["retrieve_kb"], [_retrieve, _search], None, _RO)
    assert [t.name for t in picked] == ["retrieve_kb"]


def test_executor_tools_narrows_further():
    """执行者预设 tools 再收窄（更窄者胜）。"""
    picked = select_fork_tools(
        ["retrieve_kb", "search_web"], [_retrieve, _search], ["search_web"], _RO
    )
    assert [t.name for t in picked] == ["search_web"]


def test_unknown_declared_name_is_ignored():
    """白名单里引用不存在的工具 → 忽略（不抛）。"""
    assert select_fork_tools(["ghost"], [_retrieve], None, _RO) == []


def test_empty_readonly_map_fails_closed():
    """空表 fail-closed：一个都不下发（与双轴推导的 fail-open 极性相反，有意为之）。"""
    with pytest.warns(UserWarning, match="fail-closed"):
        assert select_fork_tools([], [_retrieve, _search], None, {}) == []


def test_real_tool_pool_guard():
    """守卫：域是**真实工具池**（`make_rag_tools` + `make_task_tools`），不是 ToolRegistry
    —— `task_*` 不进注册表，遍历注册表会漏掉 D8 最担心的对象。

    以**空 allowed-tools** 装配时：结果不得含任何非只读工具、不得含禁用集成员。
    """
    from src.agents.tools.rag_tools import make_rag_tools

    pool = [
        *(
            make_rag_tools(
                MagicMock(), MagicMock(), MagicMock(), delegate_task=_delegate
            )
            or []
        ),
        *make_task_tools(),
    ]
    pool_names = {t.name for t in pool}
    assert "task_create" in pool_names, "池里应含 D8 最担心的 task_* 对象"
    assert "retrieve_kb" in pool_names

    readonly = readonly_map()
    assert readonly, "真实注册路径下只读表应已填充（build_graph 期注册，早于请求）"

    picked = select_fork_tools([], pool, None, readonly)
    picked_names = {t.name for t in picked}
    for name in picked_names:
        assert name not in FORK_FORBIDDEN_TOOLS, f"{name} 属禁用集却下发了"
        for prefix in FORK_EXCLUSIVE_TOOL_PREFIXES:
            assert not name.startswith(prefix), f"{name} 属主 agent 专属类却下发了"
        assert readonly.get(name) is True, f"{name} 非只读却进了子代理工具面"
    # D7 的交付：只读检索工具确实进入了子代理工具面
    assert "retrieve_kb" in picked_names
