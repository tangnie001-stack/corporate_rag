"""fork 子代理工具面：继承只读面 − 禁用集 − 非只读（除非显式声明）∩ 声明收窄。"""

from unittest.mock import MagicMock

import pytest
from langchain_core.tools import tool

from src.agents.skills.fork_tools import select_fork_tools
from src.agents.tools.readonly import declare_readonly, readonly_map
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
    """allowed-tools 为空表示不收窄，继承只读面。"""
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


def test_real_tool_pool_guard(monkeypatch):
    """守卫：域是**真实工具池**（`make_rag_tools` + `make_task_tools`），不是 ToolRegistry
    —— `task_*` 不进注册表，遍历注册表会漏掉 D8 最担心的对象。

    以**空 allowed-tools** 装配时：结果不得含任何非只读工具、不得含禁用集成员。

    真实池里**没有**「非只读且不以 `task_` 开头」的成员（四件套全 `readonly=True`，
    `task_*` 被前缀规则剔除），故单独跑真实池时「非只读过滤」这段回归无人拦。这里补一个
    **显式登记为写类**的替身 `write_doc`（它在 `readonly_map()` 里**有值且为 False**，
    既非缺项也非只读），使步骤②被删掉时本用例必然失败。
    """
    from src.agents.tools import readonly as readonly_module
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

    # 用 monkeypatch 锚定进程级表（`readonly_map()` 每次读该模块级字典并返回副本），
    # 再经真实注册路径 declare_readonly 记入写类替身，避免污染同文件其它用例。
    monkeypatch.setattr(readonly_module, "_TOOL_READONLY", dict(readonly))
    declare_readonly("write_doc", False)
    pool.append(_write_doc)
    readonly = readonly_map()
    assert readonly["write_doc"] is False, "写类替身须显式登记为非只读（非缺项）"

    picked = select_fork_tools([], pool, None, readonly)
    picked_names = {t.name for t in picked}
    # 步骤②（非只读过滤）的直接回归：空 allowed 下写类工具不得下发
    assert "write_doc" not in picked_names, "写类工具（非只读）不得进入子代理工具面"
    for name in picked_names:
        assert name not in FORK_FORBIDDEN_TOOLS, f"{name} 属禁用集却下发了"
        for prefix in FORK_EXCLUSIVE_TOOL_PREFIXES:
            assert not name.startswith(prefix), f"{name} 属主 agent 专属类却下发了"
        assert readonly.get(name) is True, f"{name} 非只读却进了子代理工具面"
    # D7 的交付：只读检索工具确实进入了子代理工具面
    assert "retrieve_kb" in picked_names
