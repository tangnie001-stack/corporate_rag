"""测试 SkillRegistry — 聚合/冲突/to_tool_description/懒重载。"""

from pathlib import Path

import pytest

from src.agents.skills.loader import SkillLoader
from src.agents.skills.registry import SkillRegistry


def _write_skill(
    root: Path,
    name: str,
    context: str = "inline",
    body: str = "正文",
    frontmatter_name: str | None = None,
) -> None:
    """在 root/<name>/SKILL.md 写入合法 skill；frontmatter_name 覆盖 frontmatter 的 name。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    if frontmatter_name is not None:
        fm_name = frontmatter_name
    else:
        fm_name = name
    (d / "SKILL.md").write_text(
        f"---\nname: {fm_name}\ndescription: {name} 规则\ncontext: {context}\n---\n\n{body}",
        encoding="utf-8",
    )


def test_registry_loads_all(tmp_path):
    _write_skill(tmp_path, "a")
    _write_skill(tmp_path, "b", context="fork")
    reg = SkillRegistry(SkillLoader(tmp_path))
    reg.reload_if_changed()
    assert set(reg.names()) == {"a", "b"}
    assert reg.get("a") is not None
    assert reg.get("missing") is None


def test_duplicate_frontmatter_name_fail_fast(tmp_path):
    """两个目录 frontmatter 声明同名 → reload 抛 ValueError（fail-fast 不静默覆盖）。

    这是真实冲突来源：目录名不同但 frontmatter name 相同，SkillRecord.name 取
    frontmatter name，注册表 key 冲突必须报错而非后者覆盖前者。
    """
    _write_skill(tmp_path, "dir-a", frontmatter_name="dup")
    _write_skill(tmp_path, "dir-b", frontmatter_name="dup")
    reg = SkillRegistry(SkillLoader(tmp_path))
    with pytest.raises(ValueError):
        reg.reload_if_changed()


def test_tool_description_lists_name_and_when_to_use(tmp_path):
    _write_skill(tmp_path, "finance-qa")
    reg = SkillRegistry(SkillLoader(tmp_path))
    reg.reload_if_changed()
    desc = reg.to_tool_description()
    assert "finance-qa" in desc
    assert "finance-qa 规则" in desc


def test_tool_description_truncated_by_budget(tmp_path):
    """超出预算时截断（渐进披露，完整内容命中才加载）。"""
    for i in range(20):
        _write_skill(tmp_path, f"skill-{i}")
    reg = SkillRegistry(SkillLoader(tmp_path))
    reg.reload_if_changed()
    desc = reg.to_tool_description(max_chars=120)
    assert len(desc) <= 120


def test_lazy_reload_detects_new_skill(tmp_path):
    """懒重载：目录变化（新增 skill）后 reload_if_changed 更新注册表。"""
    _write_skill(tmp_path, "a")
    reg = SkillRegistry(SkillLoader(tmp_path))
    reg.reload_if_changed()
    assert reg.names() == ["a"]
    _write_skill(tmp_path, "b", context="fork")
    reg.reload_if_changed()
    assert set(reg.names()) == {"a", "b"}


def test_lazy_reload_detects_modified_content(tmp_path):
    """懒重载：已存在 skill 内容变化也重扫（文件级 signature，非仅目录 mtime）。"""
    _write_skill(tmp_path, "a", body="第一版")
    reg = SkillRegistry(SkillLoader(tmp_path))
    reg.reload_if_changed()
    rec = reg.get("a")
    assert rec is not None
    assert rec.inline_prompt is not None
    assert "第一版" in rec.inline_prompt
    (tmp_path / "a" / "SKILL.md").write_text(
        "---\nname: a\ndescription: a 规则\n---\n\n第二版内容", encoding="utf-8"
    )
    reg.reload_if_changed()
    rec = reg.get("a")
    assert rec is not None
    assert rec.inline_prompt is not None
    assert "第二版内容" in rec.inline_prompt


def test_lazy_reload_no_change_keeps_records(tmp_path):
    """无变化时 reload 幂等，不重复解析。"""
    _write_skill(tmp_path, "a")
    reg = SkillRegistry(SkillLoader(tmp_path))
    reg.reload_if_changed()
    first = reg.get("a")
    reg.reload_if_changed()
    assert reg.get("a") is first


def _make_registry(tmp_path, frontmatter: str, name: str = "finance-qa"):
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(frontmatter, encoding="utf-8")
    registry = SkillRegistry(SkillLoader(tmp_path))
    registry.reload_if_changed()
    return registry


def test_user_visible_excludes_non_invocable(tmp_path):
    """user-invocable:false 的 skill 不出现在用户候选列表。"""
    registry = _make_registry(
        tmp_path,
        "---\nname: hidden-skill\ndescription: 仅模型可用\nuser-invocable: false\n---\n正文\n",
        name="hidden-skill",
    )

    assert registry.user_visible() == []
    assert [r.name for r in registry.model_visible()] == ["hidden-skill"]


def test_model_visible_excludes_disable_model_invocation(tmp_path):
    """disable-model-invocation:true 的 skill 不出现在模型候选列表。"""
    registry = _make_registry(
        tmp_path,
        "---\nname: manual-only\ndescription: 仅用户可调\n"
        "disable-model-invocation: true\n---\n正文\n",
        name="manual-only",
    )

    assert [r.name for r in registry.user_visible()] == ["manual-only"]
    assert registry.model_visible() == []


def test_to_tool_description_only_lists_model_visible(tmp_path):
    """delegate_task 的可用列表只含模型可见 skill。"""
    registry = _make_registry(
        tmp_path,
        "---\nname: manual-only\ndescription: 仅用户可调\n"
        "disable-model-invocation: true\n---\n正文\n",
        name="manual-only",
    )

    assert registry.to_tool_description() == "当前无可用 skill"


def test_visible_lists_are_sorted_by_name(tmp_path):
    """user_visible / model_visible 按名排序（与记录插入顺序无关）。

    目录名按 a-dir/b-dir 升序装载，frontmatter name 故意反序（zeta/alpha），
    使插入顺序 ≠ 名称顺序——只有显式排序才能通过。
    """
    _write_skill(tmp_path, "a-dir", frontmatter_name="zeta")
    _write_skill(tmp_path, "b-dir", frontmatter_name="alpha")

    registry = SkillRegistry(SkillLoader(tmp_path))
    registry.reload_if_changed()

    assert [r.name for r in registry.user_visible()] == ["alpha", "zeta"]
    assert [r.name for r in registry.model_visible()] == ["alpha", "zeta"]


def test_tool_description_exact_budget_has_no_ellipsis(tmp_path):
    """文本长度恰等于 max_chars 时不截断、不追加省略号（边界含等号）。"""
    _write_skill(tmp_path, "finance-qa")

    registry = SkillRegistry(SkillLoader(tmp_path))
    registry.reload_if_changed()

    expected = "finance-qa: finance-qa 规则"
    assert registry.to_tool_description(max_chars=len(expected)) == expected


def test_visible_lists_are_snapshots(tmp_path):
    """候选列表为快照：改动返回的 list 不影响注册表后续查询。"""
    _write_skill(tmp_path, "alpha")

    registry = SkillRegistry(SkillLoader(tmp_path))
    registry.reload_if_changed()

    first = registry.user_visible()
    first.clear()

    assert [r.name for r in registry.user_visible()] == ["alpha"]
    assert [r.name for r in registry.model_visible()] == ["alpha"]
