"""prompt 契约测试：按最终 system 载荷断言（spec <prompt-composition>）。

四条对应 design.md D5 成功度量的「自动（CI 可测）」层：
  ① 选定预设后最终 system 仍含检索阶梯要素
  ② 未注册的工具名不出现在最终 prompt
  ③ build_system_prompt 产出的工具名集合 ⊆ 实际注册工具名集合
  ④ 遍历"是否绑库 × 已注册工具集合"，每组都含完成条件与数据·指令边界
"""

import pytest

from src.config.const import EXPERT_ANALYSIS_MARKER
from src.rag.prompt import KNOWN_TOOL_NAMES, build_system_prompt

# 生产实际可能的工具集：retrieve_kb 与 ask_user 恒注册（rag_tools.py:238-239），
# search_web 受 WEB_SEARCH_ENABLED、delegate_task 受 skill 库是否有内容。
_BASE_TOOLS = frozenset({"retrieve_kb", "ask_user"})


def _tools(*extra: str) -> frozenset[str]:
    """构造工具名集合。"""
    return _BASE_TOOLS | frozenset(extra)


def _system_text(
    persona: str = "你是财务专家。",
    kb_bound: bool = True,
    has_skills: bool = False,
    tools: frozenset[str] | None = None,
    kb_domain: str = "finance",
) -> str:
    """组装一次并返回全部 system 消息拼接后的文本。"""
    if tools is None:
        tools = _tools()
    messages = build_system_prompt(
        persona=persona,
        kb_bound=kb_bound,
        has_skills=has_skills,
        tool_names=tools,
        kb_domain=kb_domain,
    )
    return "\n".join(str(m.content) for m in messages)


def test_1_preset_keeps_retrieval_ladder() -> None:
    """① 选定预设后，最终 system 仍含检索阶梯要素（阶梯住在不可替换的段）。"""
    text = _system_text(
        persona="你是财务专家，只做财务分析。",
        kb_bound=True,
        tools=_tools("search_web"),
    )
    for phrase in ("不要预先猜测问题是否在知识库范围内", "换一种问法", "top_k=10"):
        assert phrase in text, f"人设替换后丢失检索阶梯要素：{phrase}"


def test_2_absent_tool_never_named() -> None:
    """② 未注册的工具名不出现在最终 prompt（含态 A 的联网句）。"""
    text = _system_text(kb_bound=False, kb_domain="general", tools=_tools())
    assert "search_web" not in text
    assert "delegate_task" not in text


def test_3_named_tools_are_subset_of_registered() -> None:
    """③ prompt 里出现的工具名 ⊆ 实际注册工具名集合。"""
    combos = (
        _tools(),
        _tools("search_web"),
        _tools("delegate_task"),
        _tools("search_web", "delegate_task"),
    )
    for tools in combos:
        text = _system_text(tools=tools)
        mentioned = {name for name in KNOWN_TOOL_NAMES if name in text}
        assert mentioned <= tools, f"prompt 提到了未注册的工具：{mentioned - tools}"


@pytest.mark.parametrize("kb_bound", [True, False])
@pytest.mark.parametrize(
    "tools",
    [
        _tools(),
        _tools("search_web"),
        _tools("delegate_task"),
        _tools("search_web", "delegate_task"),
    ],
    ids=["no-extra", "web", "delegate", "web+delegate"],
)
def test_4_completion_and_boundary_always_present(
    kb_bound: bool, tools: frozenset[str]
) -> None:
    """④ 任何"是否绑库 × 工具集"组合下，完成条件与数据·指令边界都在。"""
    text = _system_text(kb_bound=kb_bound, tools=tools)
    assert "仅有进度更新不算完成任务" in text, "缺完成条件"
    assert "不可信的来源数据" in text, "缺数据·指令边界"


def test_web_rules_require_kb_bound() -> None:
    """态 A 下联网系列规则随适用域关闭，未绑定提示的联网允许句仍按注册出现。

    `_kb_web_rules` 的 kb_bound 半边：未绑库时第一条 system 不得出现
    sources-kb-web-rules 正文（首行「联网规则：」）；而 sources-kb-unbound-web
    判的是 search_web 是否注册，与 kb_bound 无关，仍应出现在第二条消息。
    """
    messages = build_system_prompt(
        persona="你是财务专家。",
        kb_bound=False,
        has_skills=False,
        tool_names=_tools("search_web"),
        kb_domain="finance",
    )
    assert len(messages) == 2, "态 A 应为两条 system 消息"
    first = messages[0].content
    second = messages[1].content
    assert isinstance(first, str)
    assert isinstance(second, str)
    assert "联网规则：" not in first, "未绑库时联网系列规则必须随适用域关闭"
    assert "若本轮可用联网搜索（search_web），也可联网检索" in second, (
        "联网允许句判的是 search_web 注册，与 kb_bound 无关，不得一并关闭"
    )


def test_delegate_marker_survives_segment_move() -> None:
    """委派引用规则搬到 output 段后，EXPERT_ANALYSIS_MARKER 短语仍在最终 prompt。

    该短语是 kb_citation_guardrail 的豁免键（guardrails.py:160），丢失会让护栏静默失效。
    """
    text = _system_text(tools=_tools("delegate_task"))
    assert EXPERT_ANALYSIS_MARKER in text


def test_classic_rag_path_uses_same_assembler() -> None:
    """经典 RAG 路径与 agent 路径经同一组装入口产段，不存在第二套分层模型。"""
    import inspect

    from src.rag import prompt as prompt_module

    source = inspect.getsource(prompt_module.build_prompt)
    assert "build_system_prompt" in source, "build_prompt 必须经统一入口产段"
    assert "_FALLBACK" not in source
