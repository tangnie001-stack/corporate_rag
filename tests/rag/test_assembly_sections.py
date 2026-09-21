"""段组装器：五段按序拼装、逐条判据开合、base 三选一（spec <prompt-composition>）。"""

from langchain_core.messages import SystemMessage

from src.config.prompts import loader
from src.rag.prompt import SECTION_ORDER, build_system_prompt

# 生产实际可能的工具集：retrieve_kb 与 ask_user 恒注册，search_web 受开关、delegate_task 受 skill 库
_BASE_TOOLS = frozenset({"retrieve_kb", "ask_user"})


def _tools(*extra: str) -> frozenset[str]:
    """构造工具名集合。"""
    return _BASE_TOOLS | frozenset(extra)


def test_sections_are_assembled_in_fixed_order() -> None:
    """五段均有内容时，按 base → runtime_contract → sources → tools → output 顺序拼接。"""
    messages = build_system_prompt(
        persona="你是财务专家。",
        kb_bound=True,
        has_skills=True,
        tool_names=_tools("search_web", "delegate_task"),
        kb_domain="finance",
    )
    content = messages[0].content
    assert isinstance(content, str)
    positions = [
        content.index(loader.get_content(tid).strip("\n").splitlines()[0])
        for tid in (
            "runtime-contract",
            "sources-general",
            "tools-execution",
            "output-citation",
        )
    ]
    assert positions == sorted(positions), "段顺序与 SECTION_ORDER 不一致"
    assert content.startswith("你是财务专家。")
    assert SECTION_ORDER == ("base", "runtime_contract", "sources", "tools", "output")


def test_unregistered_tool_rules_are_absent() -> None:
    """未注册 search_web 时，联网系列的段落不出现；与之无关的检索阶梯仍在。"""
    messages = build_system_prompt(
        persona="你是财务专家。",
        kb_bound=True,
        has_skills=False,
        tool_names=_tools(),
        kb_domain="finance",
    )
    content = messages[0].content
    assert "search_web" not in content
    assert "换一种问法" in content, "检索阶梯不依赖 search_web，不得被一并删掉"
    assert "按已有内容作答并说明证据不足" in content


def test_state_a_omits_retrieval_ladder() -> None:
    """未绑库（retrieve_kb 仍注册）时不出现"先检索再作答"，避免与未绑定提示矛盾。"""
    messages = build_system_prompt(
        persona="",
        kb_bound=False,
        has_skills=False,
        tool_names=_tools(),
        kb_domain="general",
    )
    content = messages[0].content
    assert "不要预先猜测问题是否在知识库范围内" not in content
    assert "换一种问法" not in content


def test_base_three_way_replacement() -> None:
    """base 三选一：预设 > 知识库领域 > 通用；三者互斥、不叠加。"""
    preset = build_system_prompt(
        persona="你是财务专家。",
        kb_bound=True,
        has_skills=False,
        tool_names=_tools(),
        kb_domain="finance",
    )[0].content
    assert isinstance(preset, str)
    assert preset.startswith("你是财务专家。")
    assert "关键指标与趋势" not in preset, "预设生效时领域 base 不得参与组装"

    domain = build_system_prompt(
        persona="",
        kb_bound=True,
        has_skills=False,
        tool_names=_tools(),
        kb_domain="finance",
    )[0].content
    assert isinstance(domain, str)
    assert domain.startswith("你是一名企业财务与投资研判助手")
    assert "关键指标与趋势" in domain

    general = build_system_prompt(
        persona="",
        kb_bound=True,
        has_skills=False,
        tool_names=_tools(),
        kb_domain="unknown-domain",
    )[0].content
    assert isinstance(general, str)
    assert general.startswith("你是一个企业知识库问答助手")


def test_empty_sections_are_dropped_without_blank_titles() -> None:
    """无条目命中的段不出现在结果里，也不留空行占位。"""
    messages = build_system_prompt(
        persona="",
        kb_bound=True,
        has_skills=False,
        tool_names=_tools(),
        kb_domain="general",
    )
    content = messages[0].content
    assert "联网规则：" not in content
    assert "委派（delegate_task）：" not in content
    assert "\n\n\n" not in content


def test_unbound_second_message_keeps_structure() -> None:
    """态 A 仍产出两条 system 消息，第二条为未绑定提示（D8 结构不变）。"""

    for tools in (_tools(), _tools("search_web")):
        messages = build_system_prompt(
            persona="",
            kb_bound=False,
            has_skills=False,
            tool_names=tools,
            kb_domain="general",
        )
        assert len(messages) == 2, tools
        assert isinstance(messages[1], SystemMessage)
        assert "请勿调用知识库检索工具" in messages[1].content
        has_web_rule = "search_web" in messages[1].content
        assert has_web_rule is ("search_web" in tools)
