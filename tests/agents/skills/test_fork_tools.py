"""fork 工具筛选：allowed-tools ∩ 执行者 tools。"""

from langchain_core.tools import tool

from src.agents.skills.fork_tools import select_fork_tools


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
def _delegate(skill: str, task: str) -> str:
    """委派。"""
    return skill


def test_empty_allowed_means_zero_tools():
    """allowed-tools 为空 → 零工具（不继承全集，D7 的 v1 语义已升级为显式声明）。"""
    assert select_fork_tools([], [_retrieve, _search]) == []


def test_allowed_filters_available():
    """只保留 allowed 里的工具。"""
    picked = select_fork_tools(["retrieve_kb"], [_retrieve, _search])
    assert [t.name for t in picked] == ["retrieve_kb"]


def test_executor_tools_narrows_further():
    """执行者预设 tools 与 allowed 求交集（更窄者胜）。"""
    picked = select_fork_tools(
        ["retrieve_kb", "search_web"], [_retrieve, _search], ["search_web"]
    )
    assert [t.name for t in picked] == ["search_web"]


def test_unknown_allowed_name_is_ignored():
    """allowed 里引用了不存在的工具 → 忽略（不抛）。"""
    assert select_fork_tools(["ghost"], [_retrieve]) == []


def test_ask_user_is_never_handed_to_sub_agent():
    """D18：子代理不持有面向用户的交互工具，即使 skill 显式声明也不给。"""
    assert select_fork_tools(["ask_user"], [_ask]) == []


def test_delegate_task_is_never_handed_to_sub_agent():
    """D7：子代理不得再委派（防递归），即使 skill 显式声明也不给。"""
    assert select_fork_tools(["delegate_task"], [_delegate]) == []


def test_forbidden_tools_do_not_block_allowed_readonly_tools():
    """禁用集只剔除自身，不影响同一白名单里的只读检索工具。"""
    picked = select_fork_tools(["ask_user", "retrieve_kb"], [_ask, _retrieve])
    assert [t.name for t in picked] == ["retrieve_kb"]
