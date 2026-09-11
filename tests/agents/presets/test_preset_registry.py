"""AgentPresetRegistry 测试：按名索引、懒重载、同名 fail-fast。"""

import pytest

from src.agents.presets.loader import AgentPresetLoader
from src.agents.presets.registry import AgentPresetRegistry


def _write(tmp_path, filename: str, name: str, desc: str = "描述"):
    (tmp_path / filename).write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n你是{name}。\n",
        encoding="utf-8",
    )


def test_index_by_name_and_get(tmp_path):
    """按名索引；不存在返回 None（调用方降级默认）。"""
    _write(tmp_path, "finance-expert.md", "finance-expert")
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path))
    registry.reload_if_changed()

    preset = registry.get("finance-expert")
    assert preset is not None
    assert preset.display_name == "finance-expert"
    assert registry.get("ghost") is None


def test_reload_picks_up_new_file(tmp_path):
    """新文件写入后 reload 可见（mtime 驱动）。"""
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path))
    registry.reload_if_changed()
    assert registry.all() == []

    _write(tmp_path, "legal-expert.md", "legal-expert")
    registry.reload_if_changed()

    assert [p.name for p in registry.all()] == ["legal-expert"]


def test_same_name_declaration_fails_fast(tmp_path):
    """两个文件声明同名 → fail-fast（禁止静默覆盖）。"""
    _write(tmp_path, "a.md", "duplicated")
    _write(tmp_path, "b.md", "duplicated")
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path))

    with pytest.raises(ValueError, match="冲突"):
        registry.reload_if_changed()


def test_missing_dir_returns_empty(tmp_path):
    """目录不存在时不抛错，all() 返回空列表。"""
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path / "agents"))
    registry.reload_if_changed()

    assert registry.all() == []


def test_all_is_sorted_by_name(tmp_path):
    """all() 按名排序，供清单接口稳定输出。"""
    _write(tmp_path, "zeta.md", "zeta")
    _write(tmp_path, "alpha.md", "alpha")
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path))
    registry.reload_if_changed()

    assert [p.name for p in registry.all()] == ["alpha", "zeta"]


def test_reload_detects_in_place_edit(tmp_path):
    """同一路径原地改写内容后 reload 可见（文件级 signature，而非仅目录 mtime）。

    原地写不改变父目录 mtime，只有文件级 signature 才能察觉内容变化。
    """
    _write(tmp_path, "finance-expert.md", "finance-expert", desc="第一版")
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path))
    registry.reload_if_changed()
    first = registry.get("finance-expert")
    assert first is not None
    assert first.description == "第一版"

    _write(tmp_path, "finance-expert.md", "finance-expert", desc="更新后的第二版")
    registry.reload_if_changed()

    updated = registry.get("finance-expert")
    assert updated is not None
    assert updated.description == "更新后的第二版"
