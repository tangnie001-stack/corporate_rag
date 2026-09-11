"""测试 SkillLoader — SKILL.md frontmatter 解析与目录扫描。"""

from pathlib import Path

from src.agents.skills.loader import SkillLoader
from src.agents.skills.models import SkillContext


def _write_skill(root: Path, name: str, frontmatter: str, body: str) -> Path:
    """在 root/<name>/SKILL.md 写入 frontmatter+正文，返回文件路径。"""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return path


def test_load_inline_skill(tmp_path):
    _write_skill(
        tmp_path,
        "finance-qa",
        "name: finance-qa\ndescription: 财务问答规则\ncontext: inline\n",
        "回答财务问题时先检索知识库，标注报告期，引用 [n]。",
    )
    records = SkillLoader(tmp_path).load_all()
    assert len(records) == 1
    rec = records[0]
    assert rec.name == "finance-qa"
    assert rec.description == "财务问答规则"
    assert rec.context == SkillContext.INLINE
    assert rec.inline_prompt is not None
    assert "先检索" in rec.inline_prompt
    assert rec.fork_body is None


def test_load_fork_skill(tmp_path):
    _write_skill(
        tmp_path,
        "finance-analyst",
        "description: 财务建模专家\ncontext: fork\nmodel: qwen3.8-max\n",
        "你是一名财务建模专家，基于给定材料做多步分析。",
    )
    records = SkillLoader(tmp_path).load_all()
    assert len(records) == 1
    rec = records[0]
    assert rec.context == SkillContext.FORK
    assert rec.model == "qwen3.8-max"
    assert rec.fork_body is not None
    assert "财务建模专家" in rec.fork_body
    assert rec.inline_prompt is None


def test_skill_name_defaults_to_dirname(tmp_path):
    """frontmatter 缺 name → 用目录名。"""
    _write_skill(
        tmp_path,
        "my-analyst",
        "description: x\ncontext: fork\n",
        "正文",
    )
    rec = SkillLoader(tmp_path).load_all()[0]
    assert rec.name == "my-analyst"


def test_description_defaults_to_body_first_paragraph(tmp_path):
    """frontmatter 缺 description → 用正文首段。"""
    _write_skill(tmp_path, "qa", "context: inline\n", "这是正文第一段。\n\n第二段。")
    rec = SkillLoader(tmp_path).load_all()[0]
    assert "这是正文第一段" in rec.description


def test_invalid_context_falls_back_inline(tmp_path):
    """context 非法值 → 回落 inline + 记 warning（不抛异常）。"""
    _write_skill(
        tmp_path,
        "bad",
        "description: x\ncontext: hybrid\n",
        "方法论正文",
    )
    rec = SkillLoader(tmp_path).load_all()[0]
    assert rec.context == SkillContext.INLINE
    assert rec.inline_prompt is not None
    assert "方法论正文" in rec.inline_prompt


def test_subdirectory_without_skill_md_ignored(tmp_path):
    """含子目录但无 SKILL.md → 忽略（不算 skill）。"""
    (tmp_path / "no-skill").mkdir()
    (tmp_path / "no-skill" / "readme.txt").write_text("x", encoding="utf-8")
    records = SkillLoader(tmp_path).load_all()
    assert records == []


def test_nested_skill_dirs_ignored(tmp_path):
    """只扫一层 skills/<name>/SKILL.md，不递归更深。"""
    _write_skill(tmp_path, "a", "description: x\n", "正文")
    _write_skill(tmp_path / "a" / "nested", "b", "description: y\n", "正文2")
    records = SkillLoader(tmp_path).load_all()
    assert len(records) == 1
    assert records[0].name == "a"
