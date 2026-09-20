"""13 条模板文件可解析、字段合法、id 唯一（12 条搬运 + 1 条新增）。"""

from pathlib import Path

import pytest
import yaml

TEMPLATES_DIR = (
    Path(__file__).resolve().parents[3] / "src" / "config" / "prompts" / "templates"
)
VALID_SECTIONS = {"base", "runtime_contract", "sources", "tools", "output"}


def _all_templates() -> list[dict]:
    """汇总所有模板文件里的模板条目。"""
    items: list[dict] = []
    for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{path.name} 根节点不是映射"
        assert "templates" in data, f"{path.name} 缺少 templates 根键"
        for entry in data["templates"]:
            items.append({"_file": path.name, **entry})
    return items


def test_template_count_and_migrated_ids() -> None:
    """13 条 = 12 条搬运 + 1 条新增 base-general（见 design.md D12.6 Q1 与 spec 的 general 保留值）。"""
    templates = _all_templates()
    assert len(templates) == 13
    migrated = {
        "tools-delegate-guidance",
        "base-financial",
        "sources-kb-unbound",
        "sources-kb-bound-discipline",
        "task-user-prompt",
        "task-classifier-system",
        "task-classifier-user",
        "task-rewrite-system",
        "task-rewrite-user",
        "task-entity-system",
        "task-entity-user",
        "output-inline-citation",
    }
    assert migrated <= {t["id"] for t in templates}, "12 条搬运模板必须齐全"


def test_ids_are_unique() -> None:
    """id 全局唯一。"""
    ids = [t["id"] for t in _all_templates()]
    assert len(ids) == len(set(ids))


def test_section_templates_have_valid_section() -> None:
    """kind=section 的模板，section 取值在允许集合内。"""
    for t in _all_templates():
        if t["kind"] == "section":
            assert t["section"] in VALID_SECTIONS, t
        else:
            assert t["kind"] == "task", t
            assert "section" not in t, f"task 模板不应有 section：{t['id']}"


def test_base_templates_have_domain() -> None:
    """section=base 的模板必须声明 domain；通用 base 用保留值 general。"""
    for t in _all_templates():
        if t.get("section") == "base":
            assert t.get("domain"), f"{t['id']} 缺少 domain"


@pytest.mark.parametrize("tid", ["base-financial"])
def test_content_is_non_empty(tid: str) -> None:
    """段模板正文非空。"""
    entry = next(t for t in _all_templates() if t["id"] == tid)
    assert entry["content"].strip()
