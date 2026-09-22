"""测试现有 skill 内容符合 frontmatter/规模/零工具约束（真实 skills 目录）。"""

from pathlib import Path

from src.agents.skills.loader import SkillLoader
from src.agents.skills.models import SkillContext
from src.config.const import INLINE_PROMPT_MAX_CHARS

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = PROJECT_ROOT / "skills"

# 当前 skill 集：fork（长文领域内容走独立子代理上下文，不占主对话历史）
FORK_SKILLS = ("financial-statement-analyzer",)


def test_skill_files_present():
    """skills/ 下每个 fork skill 的 SKILL.md 均存在。"""
    for name in FORK_SKILLS:
        assert (SKILLS_DIR / name / "SKILL.md").exists()


def test_loads_with_expected_context():
    """每个 fork skill 均解析为 fork（材料由主 agent 预检索随 task 传入）。"""
    records = {r.name: r for r in SkillLoader(SKILLS_DIR).load_all()}
    for name in FORK_SKILLS:
        assert records[name].context == SkillContext.FORK


def test_all_inline_skills_within_budget():
    """所有 inline skill 的正文 ≤ INLINE_PROMPT_MAX_CHARS（防上下文累积膨胀）。

    历史：`financial-statement-analyzer` 未声明 context → 默认 inline，正文 3002 字符
    超限 6 倍（2026-09-18 登记 docs/agents/requirements_pool.md F-13），已改
    `context: fork`。本测试保留为守卫：新增 inline skill 超限时直接失败。
    """
    records = SkillLoader(SKILLS_DIR).load_all()
    oversized = {
        r.name: len(r.inline_prompt or "")
        for r in records
        if r.context == SkillContext.INLINE
        and len(r.inline_prompt or "") > INLINE_PROMPT_MAX_CHARS
    }
    assert not oversized, (
        f"inline skill 超限（阈值 {INLINE_PROMPT_MAX_CHARS}）：{oversized}"
    )


def test_fork_prompt_must_not_mention_tool_names():
    """fork skill 正文不出现任何工具名（零工具子代理，见 design D7/D9）。"""
    records = {r.name: r for r in SkillLoader(SKILLS_DIR).load_all()}
    for name in FORK_SKILLS:
        body = records[name].fork_body or ""
        for tool_name in ("retrieve_kb", "search_web", "ask_user", "delegate_task"):
            assert tool_name not in body, f"{name} 正文不应出现工具名 {tool_name}"


def test_no_duplicate_of_system_rules():
    """skill 正文不与系统段规则逐条重复（一条事实只有一个 owner）。

    历史上 inline skill `finance-qa` 的正文与 `KB_BOUND_RETRIEVAL_DISCIPLINE` /
    回答规则 / `INLINE_CITATION_INSTRUCTION` 逐条重复，于 2026-09-18 删除。
    本测试防止同类重复被重新引入。
    """
    records = {r.name: r for r in SkillLoader(SKILLS_DIR).load_all()}
    duplicated_phrases = [
        "先调用 retrieve_kb 检索知识库，不要凭记忆作答",
        "在句末标注与检索返回一致的编号",
    ]
    for rec in records.values():
        body = rec.inline_prompt or rec.fork_body or ""
        for phrase in duplicated_phrases:
            assert phrase not in body, f"{rec.name} 正文与系统段规则重复：{phrase}"
