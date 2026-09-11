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


def test_loader_reads_readonly_table_lazily_at_parse(tmp_path):
    """loader 在解析时读取进程级只读表，而非构造期快照。

    生产时序：SkillLoader 先于工具注册构造（工具在 build_graph 内注册），
    构造期快照会漏掉后注册的工具，使"含写类工具默认锁模型端"的 fail-safe
    永不生效。本用例用 patch.dict 把进程级表临时清空（模拟构造时工具未注册），
    构造 loader 后才声明写类工具，解析仍须锁定模型端；退出时表自动还原，
    不污染同会话其它用例。
    """
    from unittest.mock import patch

    from src.agents.skills.loader import SkillLoader
    from src.agents.tools.readonly import declare_readonly

    skill_dir = tmp_path / "fixwave-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: fixwave-skill\n"
        "description: 修复波次回归用例\n"
        "allowed-tools: fixwave_publish_tool\n"
        "---\n"
        "正文\n",
        encoding="utf-8",
    )

    with patch.dict("src.agents.tools.readonly._TOOL_READONLY", {}, clear=True):
        loader = SkillLoader(tmp_path)  # 构造时进程级表为空
        declare_readonly("fixwave_publish_tool", False)  # 构造后才注册（写类）

        with pytest.warns(UserWarning, match="disable-model-invocation"):
            records = loader.load_all()

    assert records[0].disable_model_invocation is True
