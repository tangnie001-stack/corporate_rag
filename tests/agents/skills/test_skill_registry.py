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
