"""`/xxx` 前缀解析与读时清洗（design D22/D25）。"""

from pathlib import Path

from src.agents.skills.models import SkillContext, SkillRecord
from src.agents.skills.prefix import clean_prefix, parse_prefix


class _FakeRegistry:
    """只实现 get，供 parse_prefix 取 record。"""

    def __init__(self, records: list[SkillRecord]):
        self._by_name = {r.name: r for r in records}

    def get(self, name: str):
        return self._by_name.get(name)


def _fork(name: str = "finance-analyst") -> SkillRecord:
    return SkillRecord(
        name=name,
        description="d",
        context=SkillContext.FORK,
        fork_body="任务：$ARGUMENTS",
        source_path=Path(f"/tmp/{name}/SKILL.md"),
    )


def _inline(name: str = "finance-qa") -> SkillRecord:
    return SkillRecord(
        name=name,
        description="d",
        context=SkillContext.INLINE,
        inline_prompt="方法论 $ARGUMENTS",
        source_path=Path(f"/tmp/{name}/SKILL.md"),
    )


def test_plain_text_is_not_a_command():
    """不以 / 开头 → plain，task 为原文。"""
    parsed = parse_prefix("帮我分析腾讯", {"finance-analyst"}, _FakeRegistry([]))
    assert parsed.kind == "plain"
    assert parsed.task == "帮我分析腾讯"
    assert parsed.record is None


def test_known_fork_skill_is_parsed():
    """命中已注册 fork skill → known，task 为去掉前缀后的文本。"""
    registry = _FakeRegistry([_fork()])
    parsed = parse_prefix("/finance-analyst 腾讯2024", {"finance-analyst"}, registry)
    assert parsed.kind == "known"
    assert parsed.skill_name == "finance-analyst"
    assert parsed.task == "腾讯2024"
    assert parsed.record is not None
    assert parsed.record.context == SkillContext.FORK


def test_known_inline_skill_is_parsed():
    """命中已注册 inline skill → known。"""
    registry = _FakeRegistry([_inline()])
    parsed = parse_prefix("/finance-qa 毛利率怎么算", {"finance-qa"}, registry)
    assert parsed.kind == "known"
    assert parsed.record is not None
    assert parsed.record.context == SkillContext.INLINE


def test_bare_slash_is_plain():
    """/ 开头但后接空白 → 不是命令形态 → plain。"""
    parsed = parse_prefix("/ 帮我看看", {"finance-analyst"}, _FakeRegistry([]))
    assert parsed.kind == "plain"


def test_slash_path_is_plain():
    """/ 开头但含路径分隔符（非 ASCII slug）→ plain（不误判为命令）。"""
    parsed = parse_prefix("/api/v1/kbs 是什么", {"finance-analyst"}, _FakeRegistry([]))
    assert parsed.kind == "plain"


def test_unknown_skill_is_reported_not_silent():
    """形如 skill 名但未注册 → unknown（不静默按普通文本）。"""
    parsed = parse_prefix("/ghost 任务", {"finance-analyst"}, _FakeRegistry([]))
    assert parsed.kind == "unknown"
    assert parsed.skill_name == "ghost"


def test_clean_prefix_strips_known_prefix_only():
    """读时清洗：只剥已注册技能的 /name 前缀，其它原样保留（D25）。"""
    known = {"finance-analyst"}
    assert clean_prefix("/finance-analyst 腾讯2024", known) == "腾讯2024"
    assert clean_prefix("/ghost 任务", known) == "/ghost 任务"
    assert clean_prefix("普通问题", known) == "普通问题"
    assert clean_prefix("/finance-analyst", known) == ""


def test_clean_prefix_is_idempotent():
    """已清洗过的文本再清洗不变（历史消息可能被清洗多轮）。"""
    known = {"finance-analyst"}
    once = clean_prefix("/finance-analyst 腾讯2024", known)
    assert clean_prefix(once, known) == once
