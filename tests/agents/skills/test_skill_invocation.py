"""双轴调用控制默认推导测试（fail-safe：含写类工具默认锁模型端）。"""

import pytest

from src.agents.skills.invocation import derive_invocation_flags


def test_empty_allowed_tools_is_dual_open():
    """allowed-tools 为空 → 双通道开放。"""
    assert derive_invocation_flags([], {"retrieve_kb": True}) == (True, False)


def test_all_readonly_is_dual_open():
    """全部只读工具 → 双通道开放。"""
    flags = derive_invocation_flags(
        ["retrieve_kb", "search_web"], {"retrieve_kb": True, "search_web": True}
    )
    assert flags == (True, False)


def test_any_write_tool_locks_model_side():
    """含任一非只读工具 → 默认关闭模型自动调用（user 仍可显式调用）。"""
    flags = derive_invocation_flags(
        ["retrieve_kb", "publish_report"],
        {"retrieve_kb": True, "publish_report": False},
    )
    assert flags == (True, True)


def test_unknown_tool_is_treated_as_write():
    """allowed-tools 引用了未注册工具（表非空）→ 按写类处理（fail-safe，防拼错放开）。"""
    flags = derive_invocation_flags(["ghost_tool"], {"retrieve_kb": True})
    assert flags == (True, True)


def test_empty_readonly_table_falls_open():
    """工具只读表为空（尚未注册）→ 无法判断，fail-open 不锁模型端。

    否则"加载早于工具注册"这一时序会让所有带 allowed-tools 的 skill 被静默锁死。
    """
    flags = derive_invocation_flags(["retrieve_kb"], {})
    assert flags == (True, False)


def test_loader_applies_derived_flags_and_warns(tmp_path):
    """loader 会覆写未显式声明的双轴，并对含写类未声明记 warning。"""
    from src.agents.skills.loader import SkillLoader

    skill_dir = tmp_path / "report-publish"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: report-publish\n"
        "description: 发布报告\n"
        "allowed-tools: retrieve_kb, publish_report\n"
        "---\n"
        "正文\n",
        encoding="utf-8",
    )

    with pytest.warns(UserWarning, match="disable-model-invocation"):
        records = SkillLoader(
            tmp_path, tool_readonly={"retrieve_kb": True, "publish_report": False}
        ).load_all()

    assert records[0].user_invocable is True
    assert records[0].disable_model_invocation is True


def test_loader_keeps_explicit_flags(tmp_path):
    """显式声明的双轴不被推导覆写。"""
    from src.agents.skills.loader import SkillLoader

    skill_dir = tmp_path / "readonly-report"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: readonly-report\n"
        "description: 只读报告\n"
        "allowed-tools: retrieve_kb, publish_report\n"
        "disable-model-invocation: false\n"
        "---\n"
        "正文\n",
        encoding="utf-8",
    )

    records = SkillLoader(
        tmp_path, tool_readonly={"retrieve_kb": True, "publish_report": False}
    ).load_all()

    assert records[0].disable_model_invocation is False


def test_dead_skill_warns(tmp_path):
    """user-invocable false 且 disable-model-invocation true → 死 skill 记 warning。"""
    from src.agents.skills.loader import SkillLoader

    skill_dir = tmp_path / "dead-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: dead-skill\n"
        "description: 谁都用不了\n"
        "user-invocable: false\n"
        "disable-model-invocation: true\n"
        "---\n"
        "正文\n",
        encoding="utf-8",
    )

    with pytest.warns(UserWarning, match="既不可用户调用也不可模型调用"):
        records = SkillLoader(tmp_path, tool_readonly={}).load_all()

    assert len(records) == 1
