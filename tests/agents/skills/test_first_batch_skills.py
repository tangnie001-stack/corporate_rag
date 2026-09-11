"""测试首批 skill 内容符合 frontmatter/规模/零工具约束（真实 skills 目录）。"""

from pathlib import Path

from src.agents.skills.loader import SkillLoader
from src.agents.skills.models import SkillContext
from src.config.const import DELEGATE_RESULT_LIMIT, INLINE_PROMPT_MAX_CHARS

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = PROJECT_ROOT / "skills"


def test_first_batch_skill_files_present():
    """skills/ 下存在 finance-qa 与 finance-analyst 两个 SKILL.md。"""
    assert (SKILLS_DIR / "finance-qa" / "SKILL.md").exists()
    assert (SKILLS_DIR / "finance-analyst" / "SKILL.md").exists()


def test_first_batch_loads_with_expected_context():
    records = {r.name: r for r in SkillLoader(SKILLS_DIR).load_all()}
    assert records["finance-qa"].context == SkillContext.INLINE
    assert records["finance-analyst"].context == SkillContext.FORK


def test_inline_prompt_size_bounded():
    """inline skill 正文 ≤ INLINE_PROMPT_MAX_CHARS（防上下文累积膨胀）。"""
    records = {r.name: r for r in SkillLoader(SKILLS_DIR).load_all()}
    qa = records["finance-qa"]
    assert len(qa.inline_prompt or "") <= INLINE_PROMPT_MAX_CHARS


def test_fork_prompt_must_not_mention_tool_names():
    """fork skill 正文不出现任何工具名（零工具子代理，见 design D7/D9）。"""
    records = {r.name: r for r in SkillLoader(SKILLS_DIR).load_all()}
    analyst = records["finance-analyst"]
    body = analyst.fork_body or ""
    for tool_name in ("retrieve_kb", "search_web", "ask_user", "delegate_task"):
        assert tool_name not in body, f"fork skill 正文不应出现工具名 {tool_name}"


def test_fork_prompt_under_delegate_result_limit():
    """fork skill 正文（子代理 user message 任务内容）控制在合理规模内。"""
    records = {r.name: r for r in SkillLoader(SKILLS_DIR).load_all()}
    body = records["finance-analyst"].fork_body or ""
    assert len(body) <= DELEGATE_RESULT_LIMIT
